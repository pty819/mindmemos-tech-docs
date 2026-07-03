# 双存储一致性模型

MindMemOS 使用 **Qdrant**（向量+稀疏 BM25）和 **Neo4j**（图）两个持久化引擎。一条记录在写入时同时落到两个引擎，但查询路径不同。本章聚焦这个**双写模块调用链**。

## 架构总览

```{mermaid}
graph TB
    subgraph "MemoryDbWriter"
        META["apply_mutation_plan()"] --> WRITE["_write_plan()"]
        WRITE --> MERGE["合并 WritePlan + MutationPlan"]
    end
    
    subgraph "_write_plan 内部"
        MERGE --> DPS["to_db_write_primitives()<br/>mappers/db.py"]
        DPS --> SPLIT{"consistency?"}
        
        SPLIT --> |"fast (默认)"| FAST["asyncio.gather<br/>并行写入"]
        SPLIT --> |"strong"| STRONG["串行写入<br/>先 Qdrant 后 Neo4j"]
        
        FAST --> Q_UPSERT["QdrantEngine<br/>upsert_memories<br/>upsert_entities<br/>upsert_sources"]
        FAST --> N_CREATE["Neo4jClient<br/>create_relationships"]
        
        STRONG --> Q_UPSERT2["QdrantEngine upsert"]
        Q_UPSERT2 --> N_CREATE2["Neo4jClient create"]
    end
    
    Q_UPSERT --> Q_RESULT["QdrantWriteResult"]
    N_CREATE --> N_RESULT["Neo4jWriteResult"]
    Q_UPSERT2 --> Q_RESULT
    N_CREATE2 --> N_RESULT
    
    Q_RESULT --> COMBINE["合并结果<br/>记录 errors<br/>标记 graph_pending"]
    N_RESULT --> COMBINE
```

## consistency="fast"（默认路径）

```{mermaid}
sequenceDiagram
    participant P as Pipeline
    participant W as MemoryDbWriter
    participant M as mappers/db.py
    participant Q as QdrantEngine
    participant N as Neo4jClient

    P->>W: apply_mutation_plan(ctx, plan, consistency="fast")
    
    W->>W: plan.to_write_plan()
    W->>M: to_db_write_primitives(plan, ctx)
    Note over M: 将 Pipeline DTO 转为<br/>Qdrant Point + Neo4j Node
    
    M-->>W: MemoryPoint[], EntityPoint[], SourcePoint[], Relationship[]
    
    W->>W: 分离 core_entities vs search_field_entities
    
    W->>Q: asyncio.gather(
    W->>Q:   upsert_memories(memory_points)
    W->>Q:   upsert_entities(core_entity_points)
    W->>Q:   upsert_sources(source_points)
    W->>N:   create_relationships(relationships)
    W->>Q: )  # 最后加 search_field 实体写入

    Note over Q,N: 并行执行，不等待对方
    
    alt Qdrant 失败
        Q-->>W: Exception
        Note over W: consistency="fast" → 只记 log, 不抛
        W->>W: errors.append(str(exc))
    else Neo4j 失败
        N-->>W: Exception
        W->>W: errors.append(str(exc))
        W->>W: graph_pending = True
    end
    
    W-->>P: MemoryDbWriteResult(errors, graph_pending)
```

**代码锚点**：`pipelines/memory_db/writer.py:215-222`

```python
if consistency == "fast":
    qdrant_result, neo4j_result = await asyncio.gather(
        self._write_qdrant(memory_points, core_entity_points, ...),
        self._write_neo4j(ctx, plan, relationships),
    )
```

## consistency="strong" 路径

```{mermaid}
sequenceDiagram
    participant P as Pipeline
    participant W as MemoryDbWriter
    participant Q as QdrantEngine
    participant N as Neo4jClient

    P->>W: apply_mutation_plan(ctx, plan, consistency="strong")
    
    W->>Q: _write_qdrant(..., strong=True)
    Note over Q: upsert_memories + upsert_entities + upsert_sources
    
    alt Qdrant 失败
        Q-->>W: raise Exception
        W-->>P: 异常传播，不写 Neo4j
    end
    
    Q-->>W: OK
    W->>N: _write_neo4j(..., strong=True)
    
    alt Neo4j 失败
        N-->>W: raise Exception
        W-->>P: 异常传播，写入回滚
    end
```

**关键区别**：`strong` 模式下，Qdrant 失败后 Neo4j **不会写**；Neo4j 失败后 Qdrant 写入被视为**有效**（无分布式事务回滚）。

## Memory 更新/删除链

除了新建写入，MemoryDbWriter 还负责更新和删除的协调：

```{mermaid}
graph TB
    subgraph "Update Path"
        UP_REQ["update_memory(ctx, req)"] --> MUT["apply_mutation_plan()"]
        MUT --> UPD["_update_memory_command()"]
        UPD --> QPATCH["QdrantEngine.patch_memory()"]
        UPD --> NDEL["Neo4jClient.update_memory_content()"]
        UPD --> NARC["Neo4jClient.archive_memory_node()<br/>if status==archived"]
    end
    
    subgraph "Delete Path"
        DEL_REQ["delete_memory(ctx, req)"] --> MUT2["apply_mutation_plan()"]
        MUT2 --> DEL["_delete_memory_command()"]
        DEL --> HARD{"hard?"}
        HARD -->|"yes"| QDEL["QdrantEngine.delete_memory()"]
        HARD -->|"yes"| NDEL2["Neo4jClient.delete_memory_node()"]
        HARD -->|"no"| QPATCH2["QdrantEngine.patch_memory()<br/>status=archived"]
        HARD -->|"no"| NARC2["Neo4jClient.archive_memory_node()"]
    end
```

## Neo4j 写入链（_write_neo4j 内部）

```{mermaid}
graph TB
    W["MemoryDbWriter._write_neo4j()"] --> N["Neo4jClient"]
    
    N --> NMERGE["merge_memory_node(project_id, memory_id, content)"]
    N --> NMERGE2["merge_entity_node(project_id, entity_id, ...)"]
    N --> NREL["merge_relationship(source, target, rel_type)"]
    N --> NREL2["create_direct_relationship(memory_id, target_memory_id, ...)"]
    
    subgraph "Neo4jClient 内部"
        NMERGE --> |"Cypher MERGE"| CYPHER["MERGE (m:Memory {project_id, memory_id})<br/>ON CREATE SET ...<br/>ON MATCH SET ..."]
        NREL --> |"Cypher MERGE"| CYPHER2["MERGE (source)-[r:MENTIONS]->(target)<br/>ON CREATE SET ..."]
    end
```

**代码锚点**：`infra/db/neo4j.py` — 所有 Cypher 查询在此文件中定义。

## QdrantEngine 的分层

```
MemoryDbWriter._write_qdrant()
  → QdrantEngine.upsert_memories() / upsert_entities()
    → QdrantEngine._client.upsert(collection_name, points)  # AsyncQdrantClient
```

QdrantEngine 本身是不带业务语义的薄封装，只干几件事：

- 管理 `AsyncQdrantClient` 连接和并发控制
- 做 collection 级别的 CRUD
- 统一处理 payload 序列化、项目过滤

**代码锚点**：`infra/db/engine.py:38-40`

```python
class QdrantEngine:
    """Thin, business-agnostic wrapper over AsyncQdrantClient."""
```
