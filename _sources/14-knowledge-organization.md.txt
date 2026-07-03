# 知识组织方式设计

知识入库后的存储方式决定了检索效率和推理能力。本章解释 MindMemOS 为什么同时使用两种存储引擎、实体如何作为跨存储的桥接、以及多租户身份隔离如何在存储层落地。

## 双存储架构的哲学

MindMemOS 使用两个存储引擎——**Qdrant** 和 **Neo4j**——不是其中某一个的替代品，而是互补关系。

```{mermaid}
graph TB
    subgraph "Qdrant（向量存储）"
        Q1["collection: memories"]
        Q2["collection: entities"]
        Q3["collection: sources"]
        Q4["payload: 完整记忆内容"]
        Q5["vector: BM25 稀疏向量"]
    end
    
    subgraph "Neo4j（图存储）"
        N1["node: Memory"]
        N2["node: Entity"]
        N3["node: Source"]
        N4["edge: MENTIONS / RELATES_TO / ..."]
    end
    
    INPUT["Add Pipeline<br/>写入"] -->|"双写"| Qdrant
    INPUT -->|"双写"| Neo4j
    
    Qdrant -->|"检索"| SEARCH["Search Pipeline<br/>BM25 / 语义"]
    Neo4j -->|"推理"| DREAM["Dreaming Pipeline<br/>图遍历"]
    Neo4j -->|"邻居查询"| SEARCH
    
    style Qdrant fill:#4a6fa5
    style Neo4j fill:#6b4a8a
```

### Qdrant 的角色

Qdrant 是一个**检索引擎**。它的设计目标是：给定一段查询文本，快速找到最相关的 Memory。

```{mermaid}
graph LR
    Q["用户查询: '压力传感器异常'"] --> P["TextPreprocessor.preprocess_query()"]
    P --> V["SparseVectorEncoder.encode_query()"]
    V --> S["Qdrant.search_memories()<br/>→ BM25 稀疏向量匹配"]
    S --> R["返回 top-k Memory"]
```

Qdrant 中存储的不是原始文本的 embedding，而是**BM25 稀疏向量**。原因：

- **稀疏向量的可解释性**：BM25 的匹配基于实际词汇重叠，不是黑盒语义
- **零延迟预热**：无需等待 embedding 模型加载
- **领域自适应**：在专业领域（如设备故障诊断）中，专业术语的 BM25 匹配比通用语义 embedding 更精准

### Neo4j 的角色

Neo4j 是一个**推理引擎**。它的设计目标是：给定一组 Memory ID，找到它们之间的关联路径。

```{mermaid}
graph LR
    M1["Memory A: 压力异常"] -->|"MENTIONS"| E["Entity: 压力传感器"]
    M2["Memory B: MFC-1 检查"] -->|"MENTIONS"| E
    M3["Memory C: 维修记录"] -->|"MENTIONS"| E
    
    Q2["查询: '和压力传感器相关的记忆'"] --> T["Neo4j 图遍历"]
    T --> R2["返回: Memory A, B, C"]
```

Neo4j 中不存完整的记忆内容——只存节点标识和关系类型。完整的 Memory 内容在 Qdrant 中，通过 ID 回拉。

### 为什么不是单引擎方案？

| 场景 | 只用 Qdrant | 只用 Neo4j | 双用 |
|------|-----------|-----------|------|
| **全文搜索** | ✅ BM25 高效 | ❌ Cypher 全文索引慢 | ✅ Qdrant 负责 |
| **语义相似度** | ✅ embedding 搜索 | ❌ 不支持 | ✅ Qdrant 负责 |
| **实体关联查询** | ❌ 需要额外维护 | ✅ Cypher 图遍历 | ✅ Neo4j 负责 |
| **多跳推理** | ❌ 不支持 | ✅ 4 跳以内高效 | ✅ Neo4j 负责 |
| **时间线回溯** | ❌ 无内置概念 | ✅ 关系链 | ✅ Neo4j 负责 |
| **简单 KV 查询** | ✅ payload 过滤 | ❌ 需要建索引 | ✅ Qdrant 负责 |

**核心判断**：不能用一个引擎做所有事。Qdrant 擅长的（向量检索）Neo4j 做不好；Neo4j 擅长的（图遍历）Qdrant 做不了。双写不是架构冗余，是功能互补。

## 一致性模型的设计考量

双写系统面临一致性问题。MindMemOS 选择了两档一致性：

| 级别 | 行为 | 使用场景 |
|------|------|---------|
| `fast`（默认） | Qdrant + Neo4j **并行写入**，任一失败只记日志不抛异常 | 常规记忆写入 |
| `strong` | Qdrant → Neo4j **串行写入**，任一失败抛异常 | 关键记忆写入 |

```{mermaid}
sequenceDiagram
    participant P as Pipeline
    participant W as MemoryDbWriter
    
    alt fast
        P->>W: apply_mutation_plan(consistency="fast")
        W->>W: asyncio.gather(Qdrant, Neo4j)
        Note over W: 并行写入，容错
    else strong
        P->>W: apply_mutation_plan(consistency="strong")
        W->>W: Qdrant 写入
        W->>W: Neo4j 写入
        Note over W: 串行写入，失败回滚
    end
```

**设计取舍**：`fast` 模式允许 Qdrant 有数据但 Neo4j 缺失（或反之）。这意味着检索可能返回结果但图遍历不全。这在大多数场景下是可接受的——检索的召回率高于图遍历的完备性。`strong` 模式用于需要确保图推理正确的场景（如诊断结论的因果链）。

## 实体作为跨存储的桥接

实体是 Qdrant 和 Neo4j 之间的**共享标识**：

```{mermaid}
graph LR
    subgraph "Qdrant"
        QM["Memory A<br/>content: '压力异常'"]
        QE["Entity: 压力传感器<br/>search_fields: ..."]
    end
    
    subgraph "Neo4j"
        NM["(n:Memory {memory_id: A})"]
        NE["(e:Entity {entity_id: ...})"]
        NR["(n)-[:MENTIONS]->(e)"]
    end
    
    QM ---|"共享 memory_id"| NM
    QE ---|"共享 entity_id"| NE
```

Entity ID 的确定性（UUID5）保证了：两个存储中引用的同一个实体必然具有相同的 ID。这不需要分布式事务。

## 多租户知识隔离

不同项目/用户/会话的知识通过**身份上下文**在存储层隔离：

```{mermaid}
graph TB
    subgraph "Qdrant 层面"
        Q["每个 collection 的 payload 中带 project_id"]
        QF["所有查询强制附加 project_id 过滤"]
    end
    
    subgraph "Neo4j 层面"
        N["每个 node 的 property 中带 project_id"]
        NF["所有 Cypher MATCH 强制 WHERE project_id=$pid"]
    end
    
    subgraph "业务层面"
        C["MemoryRequestContext 携带完整身份链"]
        CF["project_id / user_id / session_id / agent_id 四级隔离"]
    end
```

隔离是**存储层级别**的，不是应用层级别的——即使检索 API 有 bug，Qdrant 的 filter 和 Neo4j 的 Cypher 也会自动限制结果集到当前 project。

## 为什么选择 BM25 稀疏向量而非 Dense Embedding？

BM25 稀疏向量是 MindMemOS 的默认检索表示。这不是偶然的：

```{mermaid}
graph LR
    A["原始文本"] --> B["分词/分析"]
    B --> C["hash trick → 固定维度稀疏向量"]
    C --> D["Qdrant 稀疏索引"]
    
    E["查询文本"] --> F["同路径编码"]
    F --> G["Qdrant sparse search"]
    G --> H["BM25 score 匹配"]
```

| 维度 | BM25 稀疏向量 | Dense Embedding |
|------|-------------|----------------|
| **可解释性** | 知道匹配了什么词 | 黑盒相似度 |
| **实时性** | 零预热，立即可用 | 需要 embedding 模型 |
| **领域适应** | 统计方法，自由适配 | 需要领域微调 |
| **冷启动** | 无数据也可用 | 需要语料训练 |
| **存储成本** | 稀疏，平均 50-200 非零值/文档 | 稠密，固定 768-1536 维 |

**设计中保留了对 Dense Embedding 的支持**——`SearchMode` 包含 `semantic` 和 `hybrid`，在需要时可以启用。但默认路径始终是 BM25。

## 知识组织策略总结

| 策略 | 实现方式 | 设计目的 |
|------|---------|---------|
| **双存储** | Qdrant (检索) + Neo4j (推理) | 功能互补，不做全能引擎 |
| **确定性 Entity ID** | UUID5(project_id, type, name) | 无需协调的跨存储实体合并 |
| **身份隔离** | 10 字段身份上下文 + 存储层过滤 | 多租户天然隔离，不依赖业务代码 |
| **稀疏默认** | BM25 稀疏向量 | 可解释、零预热、领域自适应 |
| **一致性可调** | fast/strong 两档 | 检索优先，推理场景强化 |
