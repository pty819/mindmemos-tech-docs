# 部署与配置

本章简要说明 MindMemOS 的部署模型和配置体系。

## 部署拓扑

```{mermaid}
graph TB
    subgraph "进程模型"
        API["API 进程<br/>uvicorn mindmemos.api:app"]
        API --> WORKERS["内嵌 Kafka Workers<br/>（同一进程内）"]
    end
    
    subgraph "外部依赖"
        Q["Qdrant<br/>向量+稀疏 BM25"]
        N["Neo4j<br/>图存储"]
        K["Kafka<br/>事件总线"]
    end
    
    subgraph "LLM 依赖"
        LLM_CHAT["Chat LLM API<br/>(OpenAI-compatible)"]
        LLM_EMBED["Embedding API"]
    end
    
    API --> Q
    API --> N
    API --> K
    WORKERS --> Q
    WORKERS --> N
    
    API --> LLM_CHAT
    API --> LLM_EMBED
```

## 配置体系

配置分层从环境变量读取，经过三层覆盖：

```{mermaid}
graph TB
    subgraph "配置来源"
        ENV["环境变量"]
        CFG["config.yaml"]
        KEY["API key 中的 override_config"]
    end
    
    subgraph "加载顺序"
        init["init_config_from_env()"] --> BASE["BaseConfig"]
        BASE --> APP["AppConfig (pipeline 选择)"]
        BASE --> ALGO["AlgoConfig (算法参数)"]
        BASE --> DB["DatabaseConfig (Qdrant/Neo4j 地址)"]
        BASE --> LLM["LLMConfig (模型/端点)"]
        BASE --> TELE["TelemetryConfig (日志/追踪)"]
    end
    
    subgraph "运行时覆盖"
        AUTH["认证阶段"] -->|"update_config()"| OVERRIDE["ContextVar 级别的 config 覆盖"]
        Note over OVERRIDE: 不污染全局 config
        Note over OVERRIDE: 仅当前请求生效
    end
```

**代码锚点**：`config/` 目录下定义了所有配置模型，顶层入口是 `config/app.py:get_config()`。

## 进程启动链

```{mermaid}
sequenceDiagram
    participant SYS as 系统启动
    participant APP as api/app.py
    participant INIT as init_config_from_env
    participant DB as ensure_database_schema
    participant LLM as init_llm/embed_client
    participant WORK as register_workers
    participant KAFKA as start_kafka

    SYS->>APP: uvicorn mindmemos.api:app
    APP->>APP: lifespan 开始
    
    APP->>INIT: init_config_from_env()
    INIT-->>APP: config 就绪
    
    APP->>APP: configure_logging / configure_tracing
    
    APP->>DB: ensure_database_schema()
    DB->>DB: Qdrant 创建 collections（如不存在）
    DB->>DB: Neo4j 创建 constraints
    
    APP->>LLM: init_llm_client() / init_embed_client()
    APP->>LLM: validate_embedding_dimension()
    
    APP->>WORK: register_workers()
    WORK->>WORK: import 6 个 worker handler
    WORK->>WORK: register_handler() × 6
    
    APP->>KAFKA: start_kafka()
    KAFKA->>KAFKA: 创建 6 个 Consumer
    KAFKA->>KAFKA: 绑定 OrderedKeyedDispatcher
    Note over KAFKA: start_kafka 在 disabled 时是 no-op
    
    APP-->>SYS: 就绪，开始接受请求
```

## Consistency Level 配置

| 配置项 | 默认值 | 含义 |
|--------|--------|------|
| `database.default_consistency` | `fast` | 新写入的默认一致性级别 |
| — | `fast` | Qdrant + Neo4j 并行写入，错误只记日志 |
| — | `strong` | Qdrant → Neo4j 串行写入，错误传播 |

在 `pipelines/add/default.py` 中通过 `_default_consistency()` 读取：
```python
def _default_consistency() -> Consistency:
    value = get_config().database.default_consistency
    return value if value in {"fast", "strong"} else "fast"
```

## K/V 总结

| 维度 | 说明 |
|------|------|
| **API 入口** | 单进程 `uvicorn mindmemos.api:app`，lifespan 内完成所有初始化 |
| **数据库** | Qdrant（向量+稀疏 BM25）+ Neo4j（图），双写不同步事务 |
| **事件总线** | Kafka，6 个 topic，OrderedKeyedDispatcher 保序 |
| **Worker 嵌入** | Worker 和 API 在同一进程内，共享同一套 Pipeline 代码 |
| **配置** | 环境变量 → config.yaml → API key override |
| **LLM** | LiteLLM 客户端，兼容 OpenAI / Anthropic / 本地 |
