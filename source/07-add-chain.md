# Add Pipeline 模块调用链

Add Pipeline 是**写入口**。本章追踪一个 `add_sync` 调用经过的模块链。

## 两种 Add Pipeline 实现

MindMemOS 有两种 Add Pipeline，通过 `memory_algorithm` 选择：

| 名称 | 注册名 | 文件 | 特点 |
|------|--------|------|------|
| DefaultAddPipeline | `default_add` | `pipelines/add/default.py` | 轻量直写，预处理+BM25 向量+实体 |
| VanillaAddPipeline | `vanilla_add` | `pipelines/add/vanilla/vanilla_add.py` | 6 阶段全量提取（LLM 密集） |
| SchemaAddPipeline | `schema_add` | `pipelines/add/schema/schema_add.py` | Schema-aware 提取 |

本章以 **DefaultAddPipeline** 为代表分析模块调用链。

## DefaultAddPipeline 调用链总图

```{mermaid}
graph TB
    subgraph "入口"
        MS["MemoryService.add()"] --> P["DefaultAddPipeline"]
    end
    
    subgraph "_build_plan()"
        P --> LOOP["遍历 messages"]
        LOOP --> TP["TextPreprocessor.preprocess_text()<br/>components/text/preprocessor.py"]
        TP -->|"normalized_text, tokens, entities, lang, bm25_text"| BUILD_MEM["构建 MemoryWrite"]
        
        BUILD_MEM --> SE["SparseVectorEncoder.encode_document()<br/>components/text/vectorizer.py"]
        SE --> VEC["构建 VectorWrite<br/>(BM25 indices + values)"]
        
        VEC --> ENT["遍历 entities"]
        ENT --> ENT_ID["_entity_id() → UUID5(project_id, type, name)"]
        ENT --> ENT_WR["构建 EntityWrite"]
        ENT_WR --> SF["_attach_search_fields()"]
        ENT_WR --> REL["构建 MENTIONS 关系"]
        
        REL --> PLAN["MemoryDbWritePlan"]
    end
    
    subgraph "写入"
        P --> W["MemoryDbWriter<br/>pipelines/memory_db/writer.py"]
        W --> Q["QdrantEngine<br/>upsert_memories/entities"]
        W --> N["Neo4jClient<br/>create_relationships"]
    end
    
    subgraph "组件依赖"
        TP --> TF["components/text/"]
        TP --> NL["_lexical.py 分词"]
        TP --> NL2["_entity.py 实体识别"]
        TP --> NL3["_normalize.py 标准化"]
        TP --> NL4["_language.py 语言检测"]
        TP --> HASH["_hashing.py content_hash"]
        
        SE --> VEC2["components/text/vectorizer.py"]
    end
```

## 同步 add_sync 完整时序

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant P as DefaultAddPipeline
    participant TP as TextPreprocessor
    participant SE as SparseVectorEncoder
    participant W as MemoryDbWriter
    participant Q as QdrantEngine
    participant N as Neo4jClient

    SVC->>P: add_sync(payload, context, add_record_id?)
    
    Note over P: === _build_plan 开始 ===
    
    loop 每条 message
        P->>TP: preprocess_text(text, segment_id)
        TP->>TP: 分词(_lexical)
        TP->>TP: 实体识别(_entity)
        TP->>TP: 标准化(_normalize)
        TP->>TP: 语言检测(_language)
        TP-->>P: PreprocessResult(normalized_text, tokens, entities, content_hash, bm25_text, lang)
        
        P->>P: 构建 MemoryWrite (memory_id=uuid4)
        P->>P: 设置 10 个身份字段(account/project/user/app/session/agent...)
        
        P->>SE: encode_document(tokens)
        SE-->>P: SparseVector(indices, values)
        
        P->>P: 构建 VectorWrite
        
        loop 每个 entity
            P->>P: _entity_id() → UUID5
            P->>P: _to_entity_write() + _attach_search_fields()
            P->>P: _to_mentions_relationship(memory_id, entity_id)
        end
    end
    
    Note over P: === _build_plan 结束 ===
    
    P->>W: apply_mutation_plan(ctx, plan, consistency)
    
    W->>W: to_db_write_primitives() → 转为 Qdrant Point / Neo4j Node
    
    alt consistency="fast"
        W->>Q: asyncio.gather upsert_memories + upsert_entities
        W->>N: create_relationships
    else consistency="strong"
        W->>Q: upsert (串行)
        W->>N: create_relationships (串行)
    end
    
    W-->>P: MemoryDbWriteResult
    
    P->>P: recorder.mark_add_completed()
    P-->>SVC: AddPipelineSyncResult(status="ok", events)
```

## 异步 add_async 的分岔

```{mermaid}
graph LR
    P["DefaultAddPipeline"] --> SYNC["add_sync()"]
    P --> ASYNC["add_async()"]
    
    ASYNC --> KMSG["构建 message dict"]
    KMSG --> PROD["get_producer().send('memory.add', message)"]
    PROD --> RET["返回 AsyncResult(status='queued')"]
    
    SYNC --> PLAN["_build_plan()"]
    PLAN --> APPLY["apply_mutation_plan()"]
```

**代码锚点**：`pipelines/add/default.py:80-105`

```python
async def add_async(self, inp, context, *, add_record_id=None, record_metadata=None):
    message = {"context": context.model_dump(...), "input": inp.model_dump(...), ...}
    await get_producer().send("memory.add", value=message, dispatch_key=memory_add_dispatch_key(context))
    return AddPipelineAsyncResult(status="queued")
```

## MemoryWrite 的 10 个身份字段

`_build_plan()` 构建 `MemoryWrite` 时会填充以下字段：

```{mermaid}
graph LR
    subgraph "MemoryRequestContext 来源"
        CTX["MemoryRequestContext"] --> ID["memory_id (uuid4)"]
        CTX --> ACC["account_id"]
        CTX --> PROJ["project_id"]
        CTX --> KEY["api_key_uuid"]
        CTX --> USR["user_id"]
        CTX --> APP["app_id"]
        CTX --> SES["session_id"]
        CTX --> AGT["agent_id"]
        CTX --> REQ["request_id"]
    end
    
    subgraph "Pipeline 计算"
        PRE["TextPreprocessor"] --> CONT["content (normalized_text)"]
        PRE --> META["metadata (content_hash, bm25_text, tokens, lang, entity_count...)"]
        PRE --> MEM_TYPE["mem_type='fact'"]
        PRE --> EXT_TYPE["mem_extract_type='vanilla'"]
    end
    
    subgraph "时间字段"
        NOW["datetime.now(UTC)"] --> C_AT["created_at"]
        EVT["inp.event_timestamp_utc"] --> VAL["validate_from"]
    end
```

这 10 个身份字段来自 `MemoryRequestContext`（由认证阶段的 `AuthContext` + 请求 body 中的 actor 字段合并而来），确保**多租户硬隔离**——不同 `project_id` 的数据在存储层就分开。
