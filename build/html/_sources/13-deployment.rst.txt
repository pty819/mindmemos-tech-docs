部署与配置
==========

部署模式
--------

MindMemOS 支持三种部署模式：

.. code-block:: text

   1. 本地开发（make dev）
      - Docker Compose: Qdrant + Neo4j + Kafka + ClickHouse + Grafana
      - FastAPI: http://127.0.0.1:8000
      - API Docs: http://127.0.0.1:8000/docs

   2. 云端服务（mindmemos.cn）
      - 托管版，注册即可获取 API Key

   3. 自部署
      - 可选 Kafka 关闭（kafka.enabled=false，但 async mode 不可用）
      - 可选 ClickHouse 关闭（telemetry.enabled=false）

配置方式
--------

配置从环境变量加载（``init_config_from_env()``）：

.. code-block:: bash

   # 基础配置
   export MINDMEMOS_CONFIG=/path/to/config.yaml

   # 数据库
   export QDRANT_URL=http://localhost:6333
   export NEO4J_URI=bolt://localhost:7687
   export NEO4J_USER=neo4j
   export NEO4J_PASSWORD=password

   # Kafka
   export KAFKA_BOOTSTRAP_SERVERS=localhost:9092

   # LLM
   export OPENAI_API_KEY=sk-xxx

Docker 档位
~~~~~~~~~~~

.. code-block:: text

   make dev-core     # Qdrant + Neo4j + Kafka + Kafka UI + kafka-exporter
   make dev          # dev-core + ClickHouse + OTel Collector + Grafana
   make dev-down     # 停止所有

``config/mindmemos/dev.yaml`` 配置三类模型路由：

.. code-block:: yaml

   chat_model_router:
     default: "openai/gpt-4.1-mini"
   embed_model_router:
     default: "openai/text-embedding-3-small"
   rerank_model_router:
     default: "cohere/rerank-english-v3.0"

   database:
     qdrant:
       vector_size: 1536
     default_consistency: "fast"

API Key 管理
~~~~~~~~~~~~

``config/mindmemos/api_keys.yaml``：

.. code-block:: yaml

   api_keys:
     - key: "mk_dev_xxx"
       account_id: "acc_001"
       project_id: "proj_001"
       rate_limit: 100

潜在问题与注意事项
------------------

.. warning::

   **异步模式需要 Kafka**

   使用 ``mode=async`` 写入时，Kafka 必须启用（``kafka.enabled=true``）。
   未启用时 ``add_async`` 会抛出 ``RuntimeError``。

.. warning::

   **向量维度一致性**

   ``config.qdrant.vector_size`` 必须与 embedding 模型的输出维度一致，
   否则 Qdrant 写入会失败。SDK 启动时 ``validate_embedding_dimension()``
   会校验这一点。

.. note::

   **快速一致性 vs 强一致性**

   - ``fast``：写入立即返回，Neo4j 图写入可能滞后（后台异步）
   - ``strong``：等待 Qdrant 确认 + Neo4j 事务提交后才返回

   默认使用 ``fast``。

.. note::

   **性能考量**

   - chunk_planner 的 token 预算直接影响 LLM 调用次数和成本
   - ``top_k`` 值建议不超过 50；过大会增加 rerank 和过滤开销
   - Dreaming 建议在低峰期执行（默认异步，通过 Kafka 调度）
