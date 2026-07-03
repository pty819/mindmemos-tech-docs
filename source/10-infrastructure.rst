基础设施层
==========

Qdrant — 向量 + 负载数据库
---------------------------

Qdrant 承担双重职责：**语义检索**和**全负载持久化**。

Collection 设计
~~~~~~~~~~~~~~~

.. code-block:: text

   Qdrant Collection     | 用途                    | Payload 模式
   ---------------------+-------------------------+-------------------------
   memory_item_v1       | 记忆点 + 稠密/稀疏向量   | MemoryWrite payload
   entity_item_v1       | 实体点                   | EntityWrite payload
   source_item_v1       | 文件/URL 来源            | SourceWrite payload
   add_record           | 操作记录（审计）          | AddRecord payload
   search_record        | 检索记录（审计）          | SearchRecord payload
   schema_add_buffer    | Schema 模式写入缓冲区     | SchemaAddBuffer payload

向量存储策略
~~~~~~~~~~~~

.. code-block:: python

   # 稠密向量配置
   vectors_config = VectorParams(
       size=model_config.embedding_dim,   # 如 1536 (text-embedding-3-small)
       distance=Distance.COSINE,
   )

   # 稀疏向量配置
   sparse_vectors_config = SparseVectorParams(
       index=SparseIndexConfig(
           full_scan_threshold=10000,     # < 1w 点全扫描
       ),
   )

Qdrant 批量写入通过 ``infra/db/qdrant_batch_writer.py`` 实现，支持：

- 内存缓冲 → 定时 flush
- 一致性级别：fast（写入即回）vs strong（等待确认）

Neo4j — 知识图谱
----------------

Neo4j 负责记忆点之间的**语义关系**，不存储负载数据（负载在 Qdrant 中）。

图数据模型
~~~~~~~~~~

.. code-block:: text

   (Memory)-[:MENTIONS]->(Entity)
   (Memory)-[:EXTRACTED_FROM]->(Source)
   (Memory)-[:RELATES_TO | RELATED_TO]->(Memory)
   (Memory)-[:HAS_PROPERTY_MEMORY]->(Memory)  # Schema 模式
   (Memory)-[:NEXT_IN_PROPERTY_TIMELINE]->(Memory)
   (Entity)-[:MENTIONED_IN_SOURCE]->(Source)
   (Entity)-[:RELATES_TO]->(Entity)
   (Memory)-[:DERIVED_FROM]->(Memory)          # 版本衍化

核心图查询路径
~~~~~~~~~~~~~~

实体扩展召回：

.. code-block:: text

   1. 搜索命中 → 获取 entity_id
   2. MATCH (e:Entity {entity_id})<-[MENTIONS]-(m:Memory)
   3. WHERE m.status = 'active' AND m.project_id = ctx.project_id
   4. RETURN DISTINCT m

记忆邻居遍历：

.. code-block:: text

   1. MATCH (seed:Memory {memory_id})-[r:RELATES_TO|RELATED_TO]-(neighbor:Memory)
   2. 可选通过共享实体桥接:
      MATCH (seed)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(neighbor)
   3. WHERE neighbor.status = 'active'
   4. RETURN DISTINCT neighbor

Kafka — 事件流与异步处理
------------------------

Kafka 用于解耦耗时操作：

.. code-block:: text

   Topic               | 用途                    | 消费者
   --------------------+------------------------+---------------------------
   memory.add          | 异步记忆写入             | workers/memory_add.py
   memory.dreaming     | 离线巩固任务             | workers/memory_dreaming.py
   memory.feedback     | 反馈处理                | workers/memory_feedback.py
   schema.add.drain    | Schema 缓冲区 drain     | workers/schema_add_drain.py
   schema.add.episode  | Schema 情节边创建       | workers/schema_add_episode.py
   skill.evolve        | Skill 自动演化          | workers/skill_evolve.py

Kafka 可关闭（``kafka.enabled=false``），此时异步操作返回错误提示。

OpenTelemetry — 可观测性
------------------------

通过 ``infra/telemetry.py`` 集成，追踪所有 Pipeline 关键路径：

- Pipeline 级别的 Span（``add.vanilla_add.sync``, ``dreaming.consolidate``）
- LLM 调用耗时
- 数据库读写耗时
- 配置项：``telemetry.enabled`` + OTel Collector endpoint

配置系统 (config/)
------------------

分层配置系统：

.. code-block:: text

   config/
   ├── app.py          — 应用级配置（数据库连接、Kafka、认证）
   ├── base.py         — BaseModel 基类
   ├── context.py      — 全局配置持有者（get_config() 单例）
   ├── validation.py   — 配置校验
   └── algo/           — 算法级配置
       ├── root.py     — 算法配置根
       ├── common.py   — 共享配置（ModelRouterConfig）
       ├── text_processing.py — 文本处理配置
       ├── dreaming.py — Dreaming 配置
       └── add/        — Add Pipeline 配置
       └── search/     — Search Pipeline 配置
       └── skill/      — Skill 配置

通过环境变量 ``MINDMEMOS_CONFIG`` 指定配置文件路径，支持 YAML 格式。

Mapper 层
---------

``mappers/`` 是 DTO 层和数据库原语的唯一桥梁：

.. code-block:: text

   mappers/
   ├── api.py           — HTTP Schema → Pipeline Input DTO
   ├── db.py            — Write DTO → Qdrant Point + Neo4j Statement
   ├── search_filters.py — DSL Filter → SearchFilter → Qdrant Filter
   ├── result.py        — Qdrant Record → MemoryView
   ├── errors.py        — 异常转换
   └── skill.py         — Skill DTO 转换
