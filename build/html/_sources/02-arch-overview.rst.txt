系统架构总览
============

MindMemOS 采用 **FastAPI + Pipeline 编排 + 双存储引擎** 的架构设计。
整个系统在 ``src/mindmemos/mindmemos`` 中组织为 8 个顶层包：

.. code-block:: text

   mindmemos/
   ├── api/              # FastAPI 路由、认证、服务层
   ├── pipelines/        # 业务 Pipeline（add / search / dreaming / feedback / ...）
   ├── components/       # 算法组件（可独立替换的子模块）
   ├── typing/           # 全系统 DTO 类型定义（零外部依赖）
   ├── mappers/          # DTO ↔ 数据库原语 转换层
   ├── infra/            # 基础设施（Qdrant / Neo4j / Kafka / OpenTelemetry）
   ├── llm/              # LLM / Embedding / Rerank 客户端抽象
   ├── config/           # 分层配置系统
   ├── prompts/          # LLM 提示词模板（EN / ZH）
   ├── workers/          # Kafka 后台消费者
   ├── errors/           # 异常体系
   └── logging/          # 日志与分布式追踪

层间依赖原则
------------

.. code-block:: text

   API (路由层)
      │ 依赖：pipelines + typing
      ▼
   Pipelines (编排层)
      │ 依赖：components + typing
      ▼
   Components (算法层)
      │ 依赖：typing（纯 DTO，无 infra 依赖）
      ▼
   Infra (基础设施层)

**关键约束**：

1. ``typing/`` 零外部依赖——不 import ``qdrant_client``、``neo4j``、``litellm``
2. ``mappers/`` 是唯一的 DTO→DB 转换桥梁
3. ``components/`` 是纯算法层，通过协议（Protocol）依赖可替换的实现
4. ``pipelines/`` 只负责编排，不包含算子实现

路由层（API）
~~~~~~~~~~~~~

FastAPI 应用定义在 ``api/app.py`` 中，通过 ``create_app()`` 工厂函数构建：

.. code-block:: python

   app = FastAPI(title="MindMemOS API", version="0.1.0", lifespan=lifespan)
   app.include_router(memory_router)    # /v1/memory/*
   app.include_router(skill_router)     # /v1/skill/*
   app.include_router(internal_router)  # 内部运维端点

认证层 (Auth)
~~~~~~~~~~~~~

支持四种认证方式，由 ``api/auth/registry.py`` 注册：

- **API Key** (默认) — Bearer token 认证，通过 ``api/auth/api_key.py`` 解析
- **Gateway JWT** — 网关代理模式
- **Internal Token** — 内部服务间通信

生命周期管理
------------

系统启动在 ``lifespan`` 上下文中按序初始化：

.. code-block:: text

   1. init_config_from_env()         # 从环境变量 / 文件加载配置
   2. configure_logging()            # 日志级别
   3. configure_tracing()            # OpenTelemetry 追踪
   4. ensure_database_schema()       # Qdrant collection + Neo4j constraint
   5. init_llm_client()             # LiteLLM 客户端
   6. init_embed_client()           # Embedding 客户端
   7. validate_embedding_dimension()# 验证 embedding 维度匹配
   8. register_workers()            # Kafka 消费者注册
   9. start_kafka()                  # 启动 Kafka 消费者

LLM 抽象层
----------

``llm/`` 包封装了三种模型路由：

.. code-block:: text

   llm/
   ├── router.py      # 从 config 读取 chat/embed/rerank 模型路由表
   ├── registry.py    # 客户端单例工厂
   ├── chat.py        # LLM 聊天补全（liteLLM 封装）
   ├── embedding.py   # 文本向量化
   └── rerank.py      # 交叉编码器重排

模型通过 ``config/algo/common.py`` 中的 ``ModelRouterConfig`` 声明式配置：

.. code-block:: yaml

   chat_model_router:
     default: "openai/gpt-4.1-mini"
     fallback: "openai/gpt-4.1-nano"
   embed_model_router:
     default: "openai/text-embedding-3-small"
   rerank_model_router:
     default: "cohere/rerank-english-v3.0"

基础设施层
----------

``infra/db/`` 实现了对双存储引擎的封装：

.. code-block:: text

   infra/db/
   ├── engine.py          # 数据库引擎初始化与生命周期
   ├── qdrant.py          # Qdrant 客户端封装（点读写、批量写入）
   ├── neo4j.py           # Neo4j 客户端封装（图查询、关联遍历）
   ├── schema.py          # Schema 管理与 collection 创建
   ├── filters.py         # 查询过滤器翻译
   ├── models.py          # 数据库层数据模型
   ├── concurrency.py     # 并发控制（乐观锁）
   ├── bootstrap.py       # 首次启动初始化
   └── registry.py        # 多集群注册表

Qdrant 负责**向量/稀疏检索**和**负载持久化**，Neo4j 负责**知识图谱关系**。
两者间的数据一致性通过 ``consistency`` 参数控制（``fast`` | ``strong``）。
