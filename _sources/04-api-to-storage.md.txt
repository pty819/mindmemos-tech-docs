# 请求链路：API → Service → Pipeline → Storage

本章追踪一个 HTTP 请求从**收到到落地**的完整模块调用链。

## 认证链

所有外部请求先经过认证模块。

```{mermaid}
sequenceDiagram
    participant C as Client
    participant RT as routes.py
    participant DEPS as deps.py
    participant AUTH as Auth Provider
    participant SVC as MemoryService

    C->>RT: POST /v1/memory/search (Authorization: Bearer <key>)
    
    RT->>DEPS: require_scopes("memory:read")
    DEPS->>DEPS: _extract_api_key(header)
    DEPS->>AUTH: resolve_api_key(key)
    
    alt auth.mode == "api_key"
        AUTH->>AUTH: APIKeyAuthProvider.resolve_api_key()
        Note over AUTH: 查数据库得到 project_id, scopes, memory_algorithm
    else auth.mode == "gateway_jwt"
        AUTH->>AUTH: GatewayJwtAuthProvider.resolve_api_key()
        Note over AUTH: 解密 JWT 得到身份和算法配置
    end
    
    AUTH-->>DEPS: ResolvedKey(account_id, project_id, memory_algorithm, scopes)
    
    DEPS->>DEPS: update_config(user_override_config)
    Note over DEPS: 请求级别的 config 覆盖（不污染全局）
    
    DEPS-->>RT: AuthContext(request_id, project_id, memory_algorithm, scopes)
    RT->>SVC: service.add/search/...(auth, request)
```

**关键连接**：认证模块产生的 `memory_algorithm` 字段（来自 API key 的绑定配置）被下游 `MemoryService` 用来**选 Pipeline**。

## MemoryService 调度链

`MemoryService` 是所有 HTTP handler 的唯一入口。它做的事情：

1. 解析 `memory_algorithm` → 选择 Pipeline
2. 转换 API schema → Pipeline DTO
3. 记录操作
4. 调用 Pipeline

```{mermaid}
graph TB
    subgraph "routes.py → MemoryService"
        RT_POST["add( )"] --> SVC_ADD["MemoryService.add()"]
        RT_POST2["search( )"] --> SVC_SEARCH["MemoryService.search()"]
        RT_POST3["get( )"] --> SVC_GET["MemoryService.get()"]
        RT_POST4["delete( )"] --> SVC_DEL["MemoryService.delete()"]
        RT_POST5["feedback( )"] --> SVC_FB["MemoryService.feedback()"]
        RT_POST6["dreaming( )"] --> SVC_DREAM["MemoryService.dream()"]
    end

    subgraph "MemoryService 内部"
        SVC_ADD --> ALGO["binding_for_memory_algorithm(auth.memory_algorithm)"]
        SVC_ADD --> MAP["to_memory_request_context()<br/>to_add_pipeline_input()"]
        SVC_ADD --> REC["record_add_input()"]
        
        ALGO --> |"add_pipeline name"| CP["create_pipeline('add', name)"]
        
        CP --> PIPE["pipeline.add_sync() / add_async()"]
        SVC_SEARCH --> CP2["create_pipeline('search', name)"]
        SVC_SEARCH --> PIPE2["pipeline.search()"]
    end

    PIPE --> |"add_sync"| W["MemoryDbWriter.apply_mutation_plan()"]
    PIPE2 --> |"search"| R["MemoryDbReader.search_sparse()"]
```

**代码锚点**：`api/services/memory_service.py:59-337`

## Pipeline → Storage 调用链

Pipeline 不直接碰数据库。它通过 `pipelines/memory_db/` 的两个门面类来读写：

```
Pipeline.add_sync()
  → self.db_writer.apply_mutation_plan(ctx, plan, consistency)
    → self._write_plan(ctx, plan)
      → self._write_qdrant(memory_points, entity_points, source_points)
      → self._write_neo4j(ctx, plan, relationships)
      
Pipeline.search()
  → self.db_reader.search_sparse(ctx, query, indices, values)
    → self._clients.qdrant.search_memories(...)
```

```{mermaid}
graph TB
    subgraph "Writing Path"
        P_ADD["Pipeline (add)"] --> W["MemoryDbWriter<br/>pipelines/memory_db/writer.py"]
        W --> |"fast mode"| G["asyncio.gather"]
        G --> QW["QdrantEngine.upsert_memories()<br/>QdrantEngine.upsert_entities()"]
        G --> NW["Neo4jClient.create_relationships()"]
        
        W --> |"strong mode"| SEQ["串行"]
        SEQ --> QW2["QdrantEngine.upsert_*()"]
        QW2 --> NW2["Neo4jClient.create_*()"]
    end
    
    subgraph "Reading Path"
        P_SEARCH["Pipeline (search)"] --> R["MemoryDbReader<br/>pipelines/memory_db/reader.py"]
        R --> QR["QdrantEngine.search_memories()"]
        R --> NR["Neo4jClient.run_read()"]
    end
```

## 同步 vs 异步路径分岔

API 调用 `add()` 时，根据 `payload.mode` 走不同路径：

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant P as Pipeline
    participant K as Kafka
    participant W as Worker
    participant DB as MemoryDbWriter

    SVC->>P: add(payload, context)
    
    alt mode="sync"
        P->>DB: apply_mutation_plan()
        DB->>DB: _write_plan() / apply_mutation_plan()
        Note over DB: 直写 Qdrant + Neo4j
        DB-->>P: WriteResult
        P-->>SVC: SyncResult(status="ok")
        SVC-->>Client: 200 OK
        
    else mode="async"
        P->>K: get_producer().send("memory.add", message)
        Note over P: message = {context, input, add_record_id}
        P-->>SVC: AsyncResult(status="queued")
        SVC-->>Client: 200 {status: "queued", add_record_id}
        
        Note over W: 异步处理
        K->>W: deliver message
        W->>P: create_pipeline("add", name).add_sync()
        Note over W,P: 同一行代码被 Worker 调用
        P->>DB: apply_mutation_plan()
    end
```

**关键设计**：Worker 和 API 的同步分支走的是**完全相同的 `add_sync()` 方法**。不重复实现。

## 路由 → Pipeline 映射表

| HTTP Endpoint | Scope | Service 方法 | Pipeline Kind | Pipeline Name 来源 |
|---------------|-------|-------------|---------------|-------------------|
| `POST /v1/memory/add` | `memory:write` | `add()` | `add` | `binding_for_memory_algorithm()` |
| `POST /v1/memory/search` | `memory:read` | `search()` | `search` | 配置 `search_pipeline` / algorithm 绑定 |
| `POST /v1/memory/get` | `memory:read` | `get()` | `get` | 配置 `default` |
| `POST /v1/memory/delete` | `memory:write` | `delete()` | `delete` | 配置 `default` |
| `POST /v1/memory/update` | `memory:write` | `update()` | `update` | 配置 `default` |
| `POST /v1/memory/feedback` | `memory:write` | `feedback()` | `feedback` | 配置 `default` |
| `POST /v1/memory/dreaming` | `memory:write` | `dream()` | `dreaming` | 配置 `default_dreaming` |
