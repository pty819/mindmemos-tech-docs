# 模块互联架构

这是本文档最核心的一张图——展示了 MindMemOS 所有子模块之间的**依赖关系**和**数据流向**。

## 全量模块依赖图

```{mermaid}
graph TB
    subgraph "L1 - API Layer (api/)"
        RT["routes.py HTTP endpoints"]
        IR["internal_routes.py internal API"]
        SR["skill_routes.py"]
        SVC["services/memory_service.py Stateless facade"]
        DEPS["deps.py Auth chain"]
    end

    subgraph "L2 - Pipeline Layer (pipelines/)"
        REG["registry.py Global dict: {type: {name: class}}"]
        BASE["base.py MemoryDbPipelineMixin"]
        ADD["add/"]
        SEARCH["search/"]
        DREAM["dreaming/"]
        FEEDBACK["feedback/"]
        GET["get/"]
        DEL["delete/"]
        UPD["update/"]
        MEMDB["memory_db/"]
    end

    subgraph "L3 - Component Layer (components/)"
        EXTR["extractor/"]
        CHUNK["chunker/"]
        SEARCHC["searcher/"]
        MM["memory_modeling/"]
        DREAMC["dreaming/"]
        FEEDBACKC["feedback/"]
        ACTIVITY["activity/"]
        TEXT["text/"]
        KAFKA_COMP["kafka/ dispatch key"]
    end

    subgraph "L4 - Infrastructure Layer (infra/)"
        DB["db/"]
        KAFKA_INFRA["kafka/"]
        TELE["telemetry/"]
        RETRY["retry/"]
    end

    subgraph "L5 - Foundation Layer"
        TYPING["typing/ DTOs"]
        CONFIG["config/"]
        LLM["llm/"]
        LOG["logging/"]
        MAPPERS["mappers/"]
        ERRORS["errors/"]
    end

    subgraph "Worker Processes (workers/)"
        W_ADD["memory_add.py"]
        W_DREAM["memory_dreaming.py"]
        W_FB["memory_feedback.py"]
        W_DRAIN["schema_add_drain.py"]
        W_EP["schema_add_episode.py"]
        W_SKILL["skill_evolve.py"]
    end

    RT --> DEPS
    DEPS -->|"resolve_api_key()"| SVC
    RT --> SVC
    IR --> SVC
    SR --> SVC

    SVC -->|"create_pipeline(type,name)"| REG
    SVC -->|"binding_for_memory_algorithm()"| ALGO["api/algorithm.py"]
    ALGO -->|"vanilla / schema routing"| REG

    REG -->|"instantiate"| ADD
    REG -->|"instantiate"| SEARCH
    REG -->|"instantiate"| DREAM
    REG -->|"instantiate"| FEEDBACK
    REG -->|"instantiate"| GET
    REG -->|"instantiate"| DEL
    REG -->|"instantiate"| UPD

    BASE -->|"provides db_reader/db_writer/recorder"| ADD
    BASE -->|"provides"| SEARCH
    BASE -->|"provides"| DREAM
    BASE -->|"provides"| FEEDBACK
    BASE -->|"provides"| GET
    BASE -->|"provides"| DEL
    BASE -->|"provides"| UPD

    ADD -->|"use"| TEXT
    ADD -->|"use"| KAFKA_COMP
    ADD --> MEMDB

    SEARCH -->|"use"| SEARCHC
    SEARCH -->|"use"| TEXT
    SEARCH --> MEMDB

    DREAM -->|"use"| DREAMC
    DREAM -->|"use"| ACTIVITY
    DREAM -->|"use"| LLM
    DREAM --> MEMDB

    FEEDBACK -->|"use"| FEEDBACKC
    FEEDBACK --> MEMDB

    MEMDB -->|"MemoryDbWriter"| DB
    MEMDB -->|"MemoryDbReader"| DB

    DB -->|"QdrantEngine"| Q["Qdrant external"]
    DB -->|"Neo4jClient"| N["Neo4j external"]

    KAFKA_INFRA -->|"OrderedKeyedDispatcher"| K["Kafka external"]

    SVC --- KAFKA_INFRA

    KAFKA_INFRA -->|"consume"| W_ADD
    KAFKA_INFRA -->|"consume"| W_DREAM
    KAFKA_INFRA -->|"consume"| W_FB
    KAFKA_INFRA -->|"consume"| W_DRAIN
    KAFKA_INFRA -->|"consume"| W_EP
    KAFKA_INFRA -->|"consume"| W_SKILL

    W_ADD --> ADD
    W_DREAM --> DREAM
    W_FB --> FEEDBACK

    LLM -->|"embed()"| EMBED_API["Embedding API"]
    LLM -->|"chat()"| LLM_API["Chat LLM API"]

    TYPING -->|"imported by all layers"| DB
    TYPING -->|"imported by all layers"| SVC
    TYPING -->|"imported by all layers"| ADD
    TYPING -->|"imported by all layers"| KAFKA_INFRA
```

**读图说明：**

- 箭头 `-->` 表示**模块 A 调用了模块 B** 的类/函数
- 虚线子图框 = Python 的 package 目录
- `typing/` 被所有模块导入（最基础的 DTO 定义层）
- `infra/db/` 只被 `pipelines/memory_db/` 调用——上层不直接碰数据库驱动
- Workers 和 API 共享同一套 Pipeline 代码（`W_ADD --> ADD`）

## 三种模块互联模式

MindMemOS 只有三种模块间连线方式，理解它们就能理解整个系统。

### 模式一：Pipeline 插件注册

这是 **Pipeline 层 → Registry → 工厂** 的互联方式。

```
load_builtin_pipelines()
  ↓ import 所有 pipeline 模块
  ↓ 每个模块的 @register(type, name) 装饰器写入
  ↓ _PIPELINE_REGISTRY = {
      "add":      {"default_add": DefaultAddPipeline, "vanilla_add": ..., "schema_add": ...},
      "search":   {"default": ..., "vanilla": ..., "schema": ..., "search_pipeline": ...},
      "dreaming": {"default_dreaming": ...},
      ...
    }

create_pipeline(type="add", name="vanilla_add")
  ↓ _PIPELINE_REGISTRY["add"]["vanilla_add"](**kwargs)
  ↓ 返回实例，自带 base.MemoryDbPipelineMixin(db_reader, db_writer, recorder)
```

**关键点**：这是典型的**控制反转**——`pipelines/registry.py` 不依赖具体 Pipeline 实现，实现反注册到 Registry。

### 模式二：Memory Algorithm 路由

这是 **API 层 → Algorithm Binding → 双 Pipeline** 的互联方式。

```
AuthContext.memory_algorithm = "vanilla"（来自 API key）
  ↓ binding_for_memory_algorithm("vanilla")
  ↓ MemoryAlgorithmBinding(add_pipeline="vanilla_add", search_pipeline="vanilla")
  ↓ 两条独立 Pipeline 被选中

add    → pipeline.add_sync() / add_async()
search → pipeline.search()
```

**关键点**：算法选择发生在**认证阶段**（API key 携带 `memory_algorithm` 字段），后续所有操作的路由在 `MemoryService` 中决定。

### 模式三：同步/异步双路径 + Kafka 事件总线

这是 **API 层 ↔ Kafka ↔ Worker** 的互联方式。

```
API POST /add mode=async
  ↓ pipeline.add_async()
  ↓ get_producer().send("memory.add", {context, input})
  ↓ 返回 200 {status: "queued"}

Kafka OrderedKeyedDispatcher（per-project_id 保序）
  ↓ consumer handle_memory_add()
  ↓ create_pipeline("add", name) — 同一行代码
  ↓ pipeline.add_sync(payload, context)
```

**关键点**：Worker 和 API 调用的是**完全相同的 `add_sync()`** 代码。区别只在入口——API 直写（同步）或投 Kafka（异步）。

## 模块互联数据汇总

| 互联模式 | 涉及的层 | 代码锚点 | 核心文件 |
|----------|---------|---------|---------|
| Pipeline 注册-工厂 | L2 Pipeline | `@register(type,name)` → `_PIPELINE_REGISTRY` | `pipelines/registry.py:15-31` |
| Algorithm 路由 | L1 → L2 | `binding_for_memory_algorithm()` → `create_pipeline()` | `api/algorithm.py:19-22` |
| 同步/异步双路径 | L1 → L2 → Kafka → Worker | `add_sync()` vs `add_async()` | `pipelines/add/default.py:57-106` |
| 双存储一致性 | L2 MemoryDb → L4 Infra | `MemoryDbWriter._write_plan()` | `pipelines/memory_db/writer.py:183-242` |
| LLM 调用 | L2 Pipeline → L5 LLM | `LLMClient.chat()` / `EmbedClient.embed()` | `llm/chat.py`, `llm/embedding.py` |
| DI 容器（Mixin） | L2 Base | `MemoryDbPipelineMixin.__init__()` | `pipelines/base.py:21-33` |
