类型系统（typing/）
===================

MindMemOS 的类型系统是整套架构的基石，约 3000 行 Pydantic 模型，划分为 7 个文件，
**不依赖任何数据库或 LLM 库**。这种分层设计的核心原则是：

**DTO 层零外部依赖** — Pydantic 是唯一的第三方包，不 import ``qdrant_client``、
``neo4j``、``litellm`` 或任何基础设施库。所有到数据库原语的转换都在 ``mappers/`` 中完成。

类型分层
--------

.. code-block:: text

   typing/
   ├── memory.py     # 业务 DTO（Entity, MemoryWrite, SearchFilter...）
   ├── memory_db.py  # 数据库操作 DTO（Query, WritePlan, Mutation...）
   ├── service.py    # Pipeline I/O 合约（Input, Result）
   ├── algo.py       # 算法中间 DTO（Chunk, Turn, SparseVector...）
   ├── llm.py        # LLM 交互 DTO（ChatResponse, EmbeddingResponse）
   ├── activity.py   # 活动记录 DTO（ActivityScope, RecentActivityBundle）
   └── skill.py      # Skill 管理 DTO（SkillBlob, SkillVersion...）

**memory.py** — 业务核心
-------------------------

定义最关键的领域模型：

内存类型 (MemoryType)
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   MemoryType = Literal[
       "profile",      # 用户画像（偏好、背景）
       "fact",         # 事实性记忆
       "experience",   # 经验性记忆
       "episodic",     # 情景记忆
       "tool_trace",    # 工具调用记录
       "skill_candidate",  # 可沉淀的 skill 候选
       "file_knowledge",   # 文件知识
   ]

图关系标签 (Graph Relationship)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

系统定义了 7 类 Neo4j 边：

.. code-block:: python

   REL_HAS_PROPERTY_MEMORY     = "HAS_PROPERTY_MEMORY"       # 实体→属性记忆
   REL_NEXT_IN_PROPERTY_TIMELINE = "NEXT_IN_PROPERTY_TIMELINE" # 属性时间线连接
   REL_RELATES_TO              = "RELATES_TO"                # 记忆间强关联
   REL_RELATED_TO              = "RELATED_TO"                # 记忆间弱关联
   REL_MENTIONS                = "MENTIONS"                  # 记忆→实体引用
   REL_EXTRACTED_FROM          = "EXTRACTED_FROM"            # 记忆→来源
   REL_MENTIONED_IN_SOURCE     = "MENTIONED_IN_SOURCE"       # 实体→来源
   REL_DERIVED_FROM            = "DERIVED_FROM"              # 记忆版本衍化

SearchFilter — 查询过滤器 DSL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

提供类似 Qdrant 的布尔过滤树，但完全独立于 ``qdrant_client`` 类型：

.. code-block:: python

   class SearchFilter(BaseModel):
       must: list[FieldCondition | SearchFilter]
       should: list[FieldCondition | SearchFilter]
       must_not: list[FieldCondition | SearchFilter]

   class FieldCondition(BaseModel):
       field: str
       op: FilterOp  # match | any | except | text | range | datetime | is_empty | is_null
       value: str | int | float | bool | None = None
       gt / gte / lt / lte: ...

通过 ``DSL_FILTERABLE_MEMORY_FIELDS`` 白名单限制用户可过滤的字段。

MemoryWrite — 存储写 DTO
~~~~~~~~~~~~~~~~~~~~~~~~~

写入时的完整 payload 定义：

.. code-block:: python

   class MemoryWrite(BaseModel):
       memory_id: str
       account_id / project_id / api_key_uuid: str  # 多租户隔离
       user_id / app_id / session_id / agent_id: str | None  # 身份维度
       content: str                         # 记忆文本
       mem_type: MemoryType = "fact"        # 记忆类型
       mem_extract_type: str = "vanilla"    # 提取算法标识
       metadata: dict                       # {content_hash, bm25_text, tokens, lang, ...}
       validate_from / validate_to: datetime|None  # 时效窗口
       status: MemoryStatus = "active"       # active | archived | delete
       reinforcement_count: int = 0         # 强化计数
       parent_ids / root_id: list[str]      # 版本链追踪
       property_name / entity_id / entity_type: str|None  # schema 关联

**service.py** — Pipeline 合约
------------------------------

定义 Pipeline 的输入输出模型。例如 AddPipelineInput：

.. code-block:: python

   class AddPipelineInput(BaseModel):
       messages: list[DialogueMessage | UrlMessage | FileMessage | TextMessage]
       mode: AddMode = "sync"        # sync | async
       force_generation: bool = False
       metadata: dict = {}

   class AddPipelineSyncResult(BaseModel):
       status: ServiceResultStatus    # ok | error | queued
       memories: list[MemoryAddEventItem]

**algo.py** — 算法中间 DTO
--------------------------

包含 chunk 化、抽取、反馈等算法阶段的中间产物：

- ``Turn`` / ``Chunk`` / ``ExtractionEnvelope`` — 对话分割与打包
- ``ConsolidationAction`` / ``ConsolidationCreate`` / ``ConsolidationMerge`` — Dreaming 动作
- ``SparseVector`` / ``BM25TokenizationResult`` — 稀疏检索中间结果
- ``ImplicitFeedbackSignal`` — 隐式反馈信号

**memory_db.py** — 数据库操作 DTO
----------------------------------

定义了数据库层操作的原语：

.. code-block:: python

   class MemoryDbSearchQuery(BaseModel):
       query: str
       top_k: int = 10
       filters: SearchFilter | None = None
       mode: SearchMode         # semantic | bm25 | rrf | graph | hybrid
       ranking: RankingMode     # none | score | hybrid

   class MemoryDbMutationPlan(BaseModel):
       memories: list[MemoryDbMemoryWriteCommand]
       entities: list[MemoryDbEntityWriteCommand]
       updates: list[MemoryDbMemoryUpdateCommand]
       deletes: list[MemoryDbDeleteCommand]
       relationships: list[MemoryDbRelationshipWriteCommand]
       ...
