================================================================
MindMemOS 技术设计理念与模块深度解析
================================================================

.. raw:: html

   <style>
   .design-principle { background: #f0f8ff; padding: 12px; border-left: 4px solid #1e90ff; margin: 12px 0; }
   .arch-decision { background: #fff8f0; padding: 12px; border-left: 4px solid #ff8c00; margin: 12px 0; }
   .code-insight { background: #f0fff0; padding: 12px; border-left: 4px solid #228b22; margin: 12px 0; }
   </style>

.. |br| raw:: html

   <br />

.. contents:: 目录
   :depth: 3
   :local:

============================================
第一部分：总体设计理念
============================================

MindMemOS 不只是一个"给 Agent 加记忆"的工具库，而是一套**完整的记忆操作系统**
——它有自己明确的架构哲学、分层契约和演化机制。

1.1 核心设计原则
================

1.1.1 分层隔离，契约驱动
------------------------

整个系统遵循严格的**单向依赖**原则：

.. code-block:: text

   API (路由层)           ← 依赖 pipelines + typing
      │
   Pipelines (编排层)     ← 依赖 components + typing
      │
   Components (算法层)    ← 依赖 typing (纯 DTO)
      │
   Infra (基础设施层)     ← Qdrant / Neo4j / Kafka 具体实现

.. admonition:: 设计原则

   **原则一：typing/ 包零外部依赖**

   ``mindmemos/typing/`` 中的 Pydantic 模型不 import 任何数据库客户端
   （``qdrant_client``、``neo4j``）、LLM 库（``litellm``）或序列化框架。
   这是整套架构的基石——类型定义是"契约"，不是"实现"。

.. admonition:: 设计原则

   **原则二：Pipeline 是编排器，不是执行器**

   pipelines/ 中的每个 Pipeline 只做三件事：
   1. 接收输入 DTO
   2. 按阶段调用 components/
   3. 返回输出 DTO

   Pipeline 不包含任何算子实现。算子全在 components/ 中。这使得 Pipeline
   可以被完整替换而不动算法，算法可以被单独测试而不动编排逻辑。

.. admonition:: 设计原则

   **原则三：mappers/ 是唯一的类型转换桥梁**

   DTO 层（typing/）和数据库原语（Qdrant PointStruct / Neo4j Cypher）
   之间的所有转换都在 mappers/ 中完成。这保证了：
   - 数据库层可以独立升级（Qdrant API 变化只影响 mapper）
   - 业务模型变化不影响存储结构
   - 类型错误在 mapper 边界被捕获，不会渗透到业务逻辑中

1.1.2 Protocol 优先，而非继承
-----------------------------

MindMemOS 大量使用 Python ``Protocol`` 定义接口契约，而非抽象基类：

.. code-block:: python

   class AddPipeline(Protocol):
       async def add_sync(self, inp: AddPipelineInput, context: MemoryRequestContext) -> AddPipelineSyncResult
       async def add_async(self, inp, context) -> AddPipelineAsyncResult

   class SearchEngine(Protocol):
       name: str
       async def search_candidates(self, inp, context, *, options=None) -> list[MemorySearchItem]

.. admonition:: 架构决策

   **为什么用 Protocol？**

   1. **结构子类型化**：只要对象有对应方法签名，就自动满足协议，不需要显式
      继承或注册。这让测试 mock 极其简单。
   2. **依赖倒置**：调用方依赖 Protocol（抽象），而非具体类。Pipeline 切换
      引擎时，只要新引擎实现了 Protocol，零改动。
   3. **编译期检查**：mypy/pyright 会在传入不符合 Protocol 的对象时报错。

1.1.3 注册表模式——字符串路由
----------------------------

不同 Pipeline 实现通过 ``pipelines/registry.py`` 的 ``@register`` 装饰器注册：

.. code-block:: python

   @register(type="add", name="vanilla_add")
   class VanillaAddPipeline(MemoryDbPipelineMixin): ...

   @register(type="search", name="vanilla_search_engine")
   class VanillaSearchEngine(MemoryDbPipelineMixin): ...

运行时通过配置字符串（如 ``search_pipeline: "vanilla"``）选择实现。

.. admonition:: 架构决策

   **为什么不用 import 时直接硬编码？**

   1. **热切换能力**：配置驱动的 Pipeline 选择允许在不同环境（dev/prod）
      使用不同策略，或在 A/B 测试中并行运行。
   2. **Schema 模式可插拔**：vanilla 和 schema 是两套完全不同的 add pipeline，
      通过 ``algo_config.add.vanilla.enable_schema`` 切换，无需代码修改。
   3. **测试友好**：测试用例可以注册 mock pipeline 而不用修改生产代码。

1.1.4 构造函数注入（DI）
------------------------

所有 Pipeline 和 Component 的依赖通过构造函数传入：

.. code-block:: python

   class VanillaAddPipeline(MemoryDbPipelineMixin):
       def __init__(self, *, text_preprocessor=None, memory_extractor=None,
                    candidate_deduplicator=None, related_memory_recall=None,
                    safety_gate=None, vectorizer=None, llm_client=_CLIENT_UNSET, ...):
           # 未传入时使用默认实现
           self._text_preprocessor = text_preprocessor or get_text_preprocessor(cfg)
           self._llm_client = self._resolve_llm(llm_client)

.. admonition:: 架构决策

   **设计意图**：

   - **可测试性**：每个组件可独立 mock 替换
   - **可选默认值**：未显式注入时自动使用全局单例，降低使用成本
   - **延迟解析**：``_CLIENT_UNSET`` 哨兵值表示"未设置"，在构造函数内部
     才尝试解析全局 LLM client。这使 Pipeline 可在 LLM 未初始化时就创建。

1.2 三层记忆模型
================

MindMemOS 将记忆分为三个层次，分别对应不同的处理路径：

.. list-table:: 三层记忆架构
   :header-rows: 1

   * - 层次
     - 存储维度
     - 处理 Pipeline
     - 典型场景
   * - L1: Flat Memory
     - Qdrant (向量 + 负载)
     - DefaultAddPipeline / VanillaAddPipeline
     - 事实记忆、对话片段
   * - L2: Schema Memory
     - Qdrant + Neo4j (实体-属性-时间线)
     - SchemaAddPipeline + SchemaDrain 异步流
     - 用户画像、偏好演化
   * - L3: Consolidation Memory
     - Dreaming Pipeline (离线演化)
     - LLM 驱动的合并/更新/归档
     - 知识蒸馏、冗余消除

.. admonition:: 架构决策

   **为什么要三层？**

   简单方案（如 Mem0）只有 L1：写入→向量化→检索。这在对话记忆上表现尚可，
   但无法处理"用户的偏好随时间变化"这类复杂场景。L2 通过实体-属性建模解决
   "用户 X 的咖啡偏好从冰美式变成了拿铁"这类时间线追踪。L3 则通过离线
   Dreaming 解决"大量碎片化记忆怎么自动合并成精炼知识"的问题。

============================================
第二部分：模块级设计深度解析
============================================

2.1 typing/ — DTO 契约层
=========================

文件结构
--------

.. code-block:: text

   typing/
   ├── memory.py      # 495行 — 核心业务模型（MemoryWrite, Entity, SearchFilter...）
   ├── memory_db.py   # 433行 — 数据库操作 DTO（MemoryDbWritePlan, MutationPlan...）
   ├── service.py     # 395行 — Pipeline I/O 合约（Input, Result）
   ├── algo.py        # 491行 — 算法中间 DTO（Turn, Chunk, ExtractionEnvelope...）
   ├── activity.py    # 178行 — 活动记录 DTO（ConversationActivity, RecentActivityBundle）
   ├── llm.py         #  36行 — LLM 交互 DTO（ChatResponse, EmbeddingResponse）
   └── skill.py       # 256行 — Skill 版本管理 DTO

核心设计决策
------------

**memory.py vs memory_db.py 分离**

这是 MindMemOS 最微妙的设计决策之一：

.. code-block:: text

   memory.py (业务层)          memory_db.py (存储操作层)
   ──────────────────────    ──────────────────────────
   MemoryWrite               MemoryDbWritePlan
   Entity                    MemoryDbSearchQuery
   SearchFilter              MemoryDbSearchHit
   MemoryView                MemoryDbMutationPlan
   GraphRelationship         MemoryDbMemoryWriteCommand

.. admonition:: 架构决策

   **分离理由**：

   1. **关注点分离**：memory.py 描述"记忆是什么"，memory_db.py 描述
      "怎么操作数据库"。同一份业务数据（MemoryWrite）可以有不同的存储操作
      （写入、更新、删除）。

   2. **防止泄漏**：如果 MemoryWrite 直接可被 MutationPlan 使用，业务逻辑
      就可能绕过 mutation 语义检查直接写 DB。中间的 MemoryDb*Command 层
      强制执行操作类型（upsert/patch/delete）。

   3. **演化自由度**：添加新的存储后端（如 PostgreSQL）只需新增
      MemoryDb*Command 映射，不影响现有业务模型。

**SearchFilter — 与 Qdrant 解耦的过滤器 DSL**

.. code-block:: python

   class SearchFilter(BaseModel):
       must: list[FieldCondition | SearchFilter]
       should: list[FieldCondition | SearchFilter]
       must_not: list[FieldCondition | SearchFilter]

   class FieldCondition(BaseModel):
       field: str
       op: FilterOp  # match | any | except | text | range | datetime | is_empty | is_null
       value / values / gt / gte / lt / lte: ...

.. admonition:: 代码洞察

   这套 Filter 系统完全模仿 Qdrant 的 bool filter 结构，但**不依赖**
   ``qdrant_client`` 包。转换由 ``mappers/search_filters.py`` 完成。

   这么做的原因：Qdrant 的 Filter 模型有大量细碎的 import 依赖
   （``qdrant_client.models.FieldCondition``、``qdrant_client.models.MatchValue``...），
   如果 typing/ 层直接依赖它们，任何想使用 MemoryDTO 的客户端都必须安装
   ``qdrant_client``。现在的设计让 SDK 用户可以只用 ``pip install mindmemos-sdk``
   就完成 DTO 操作。

**关系常量集中管理**

.. code-block:: python

   REL_HAS_PROPERTY_MEMORY     = "HAS_PROPERTY_MEMORY"
   REL_NEXT_IN_PROPERTY_TIMELINE = "NEXT_IN_PROPERTY_TIMELINE"
   REL_RELATES_TO              = "RELATES_TO"
   REL_RELATED_TO              = "RELATED_TO"
   REL_MENTIONS                = "MENTIONS"
   REL_EXTRACTED_FROM          = "EXTRACTED_FROM"
   REL_MENTIONED_IN_SOURCE     = "MENTIONED_IN_SOURCE"
   REL_DERIVED_FROM            = "DERIVED_FROM"

所有图边类型在 ``typing/memory.py`` 中集中定义为字符串常量，而非分散在
各个 mappers/pipelines/中硬编码。这避免了"Neo4j 里的边类型拼错"这类低级的
运行时错误。

**Turn 和 Chunk — 对话结构化的中间表示**

.. code-block:: python

   class TurnMessageRef:     # 一条消息的元信息
       text, role, timestamp, message_index, is_extractable

   class Turn:               # 一组消息形成一个"对话回合"
       messages: list[TurnMessageRef]
       boundary: TurnBoundary        # complete | open_head | open_tail | orphan

   class Chunk:              # 发送给 LLM 提取的 token 预算单位
       turns: list[Turn]
       boundary: ChunkBoundary
       token_count: int
       needs_compaction: bool

   class ExtractionEnvelope: # LLM 提取的结构化上下文
       extractable_messages      # 可提取证据
       current_context_messages  # 不可提取的上下文
       history: HistoryPack      # 跨 chunk 历史窗口
       recalled_memories         # 相关记忆召回
       boundary                  # 边界类型

.. admonition:: 架构决策

   **为什么引入 Turn 和 Chunk？**

   原始 AddPipeline（DefaultAddPipeline）直接逐条文本提取，没有对话结构概念。
   但对于真实的 Agent 对话，需要：

   1. **识别对话边界**：用户说"帮我查天气"→ 助手回复"今天25度"是一个
      Turn；连续的用户消息（没有助手回复）则是 open_tail。

   2. **控制 token 预算**：一个对话可能数百轮，不能全部塞进一个 LLM 调用。
      Chunk 是 token 预算的硬边界。

   3. **历史滑动窗口**：Chunk N 可以看到 Chunk N-1 的历史摘要，但不需要
      全部原始文本。

   4. **边界感知**：open_tail 的 chunk 不应被视为完整对话，extractor 要在
      指令中明确说明保守程度。

2.2 config/ — 分层配置系统
==========================

MindMemOS 的配置系统有四个层次：

.. code-block:: text

   层次 0: 默认值（代码硬编码）
   层次 1: YAML 配置文件（config/mindmemos/dev.yaml）
   层次 2: 环境变量覆盖（MINDMEMOS_* 系列）
   层次 3: 请求级覆盖（tenant_config/project_config，通过 update_config()）

**OmegaConf 结构化配置**

使用 OmegaConf 将 YAML 映射到 ``@dataclass`` 结构体：

.. code-block:: python

   @dataclass
   class MemoryConfig:
       database: DatabaseConfig
       kafka: KafkaConfig
       telemetry: TelemetryConfig
       algo_config: MemoryAlgoConfig

算法的配置按 pipeline 分组：

.. code-block:: text

   algo_config
   ├── common                       # 共享（prompt_language）
   ├── text_processing              # 文本预处理（spaCy 模型、BM25 参数、hash 维度）
   ├── add
   │   ├── vanilla                  # 6 阶段 pipeline 参数（token budget、recall、safety gate）
   │   └── schema                   # Schema 模式参数（chunker、drain、extraction、merge）
   ├── search
   │   ├── default                  # BM25 检索
   │   ├── vanilla                  # 混合检索（dense+sparse+graph）
   │   ├── schema_search            # Schema 检索（entity/property）
   │   └── agentic                  # 多轮推理检索
   ├── dreaming                     # 离线巩固（lookback_days、concurrency）
   └── skill_evolution              # Skill 演化

.. admonition:: 代码洞察

   **text_processing 是 frozen 字段**

   在 ``MemoryAlgoConfig`` 中：

   .. code-block:: python

       text_processing: TextProcessingConfig = frozen_field(...)

   ``frozen_field`` 意味着该配置项的 tenant/project 级覆盖被禁止。原因是
   ``TextPreprocessor`` 共享全局的 spaCy 模型和 hash 状态，不可能为每个
   请求切换不同的配置。这是一个安全边界：防止用户通过 API 参数意外重载
   文本预处理行为。

2.3 infra/db/ — 双存储引擎
===========================

MindMemOS 采用 **Qdrant + Neo4j 双引擎** 架构，各有明确职责：

.. list-table:: 存储引擎分工
   :header-rows: 1

   * - 能力
     - Qdrant
     - Neo4j
   * - 向量检索
     - Dense + Sparse 混合检索
     - （不支持）
   * - 负载持久化
     - 全量 payload 存储
     - （不支持）
   * - 实体去重
     - 通过 payload 过滤
     - Node key 约束（确定性）
   * - 关系遍历
     - 不支持 N 跳遍历
     - MEMORY→ENTITY→MEMORY 图遍历
   * - 写入一致性
     - fast / strong 两种模式
     - ACID 事务

.. admonition:: 架构决策

   **为什么不只用其中一种？**

   - 只用 Qdrant：无法做语义关系推理。"找到喜欢冰美式的用户"是向量搜索，
     但"找和 X 用户偏好相似的用户"需要图遍历。
   - 只用 Neo4j：向量检索需要第三方插件（neo4j-vector），且稀疏向量
     （BM25 hash-trick）支持不成熟。

   双引擎的代价是**写入一致性维护**：一次 add 操作需要协调 Qdrant upsert
   和 Neo4j MERGE。``consistency`` 参数控制行为：
   - ``fast``：先写 Qdrant，Neo4j 异步写入。检索可能出现图结果延迟。
   - ``strong``：两者在一个事务语义中完成。代价是吞吐下降。

**Qdrant 引擎设计**

.. code-block:: text

   QdrantEngine (轻量级封装)
   ├── ensure_collection()       # 自动创建 collection 和 payload index
   ├── upsert/retrieve/scroll/query/delete/batch_update
   ├── project_filter()          # 强制注入 project_id 条件
   ├── safe_payload()            # 递归转换 datetime/dict/list 为 Qdrant 安全类型
   └── _batch_writer             # 可选批量写入器

   Collection 结构 (6 个):
   ├── memory_item_v1            # 记忆点 (dense + sparse 向量 + payload)
   ├── entity_item_v1            # 实体点 (同上)
   ├── source_item_v1            # 来源点 (仅 payload)
   ├── add_record_v1             # 写入审计记录 (仅 payload)
   ├── search_record_v1          # 检索审计记录 (仅 payload)
   └── schema_add_buffer_v1      # Schema 写入缓冲区 (仅 payload)

.. admonition:: 代码洞察

   **project_id 硬隔离**

   每个 Qdrant 查询都通过 ``project_filter()`` 强制注入 ``project_id`` 条件，
   且该条件是 ``must`` 列表的第一项。这样即使 API 层忘记传 filter，
   数据也不会跨项目泄露。

   **QdrantBatchWriter 设计**

   .. code-block:: python

       class QdrantBatchWriter:
           def __init__(self, upsert_fn, batch_size=100, flush_interval_ms=1000, ...):
               self._buffer: dict[str, list[PointStruct]] = {}
               self._flush_task: asyncio.Task

           async def upsert(self, collection, points):
               # 加入缓冲区 → 达到 batch_size 则立即 flush
               # 否则等待 flush_interval 超时后批量 flush

   批量写入器在写入压力大时将多次小 upsert 合并为一次大 upsert，减少
   HTTP 往返和 Qdrant WAL 压力。

**Neo4j 引擎设计**

Neo4jStore 专注于**关系操作**，不分担负载存储：

.. code-block:: text

   MEMORY(project_id, memory_id)        # 节点属性：content, status
      │
      ├─[:MENTIONS]→ ENTITY(project_id, entity_id)   # 记忆→实体
      ├─[:EXTRACTED_FROM]→ SOURCE(...)                # 记忆→来源
      ├─[:RELATES_TO|RELATED_TO]→ MEMORY              # 记忆→记忆
      ├─[:HAS_PROPERTY_MEMORY]→ MEMORY                # Schema 属性关联
      └─[:NEXT_IN_PROPERTY_TIMELINE]→ MEMORY          # 属性时间线

.. admonition:: 代码洞察

   所有 Neo4j 查询的核心模式是**图邻居扩展**：

   查询入口：匹配候选记忆 → 通过 MENTIONS 找到关联实体 →
   通过实体反向 MENTIONS 找到更多记忆 → 通过 RELATES_TO 找到直接邻居
   → RRF 融合所有候选

2.4 llm/ — LLM 抽象层
=======================

LLM 抽象层基于 liteLLM，但增加了业务语义封装：

.. code-block:: text

   LLMClient (chat.py)
   ├── chat(task, messages, format_parser) -> ChatResponse
   │   - task: 用于追踪和日志的记忆任务名称
   │   - format_parser: 可选的结构化输出解析器（如 parse_memory_extraction_json）
   │   - 内部使用 liteLLM 的 acustom_client 或 factory
   │
   EmbedClient (embedding.py)
   │   embed(texts) -> EmbeddingResponse
   │   embed_dimension() -> int
   │
   RerankClient (rerank.py)
   │   rerank(query, texts) -> RerankResponse
   │
   ModelRouter (router.py)
   │   从 config 读出 chat/embed/rerank 模型路由表
   │   支持 fallback 链（default → fallback）
   │
   LLMRegistry (registry.py)
       全局单例工厂：get_llm_client() / get_embed_client()

.. admonition:: 架构决策

   **为什么要二次封装 liteLLM 而非直接使用？**

   1. **任务标签**：``chat(task="memory.add.extract", ...)`` 让 OpenTelemetry
      追踪可以直接看到"这一步 LLM 调用是用于记忆提取"。liteLLM 本身不提供
      业务上下文的埋点。

   2. **结构化输出**：``format_parser`` 参数让 LLM 输出从原始 JSON 解析为
      业务 DTO 的逻辑收在调用点附近。

   3. **配置路由**：不同任务可以使用不同模型（extraction 用 gpt-4.1-mini、
      dreaming 用 gpt-4.1-nano），通过 router 配置在 YAML 中声明式指定。

2.5 components/ — 算法组件库
==============================

这是 MindMemOS 最庞大的模块，约 80+ 源文件。按职责划分：

2.5.1 components/text/ — 文本处理流水线
----------------------------------------

``TextPreprocessor`` 是文本处理的主编排器：

.. code-block:: text

   preprocess_text(text, segment_id)
       │
       ├── 1. 语言检测 (_language.py)
       │     - 基于 Unicode 字符比例（CJK / Latin）
       │     - 输出：zh / en / mixed / unknown
       │
       ├── 2. 文本归一化 (_normalize.py)
       │     - Unicode NFKC 归一化
       │     - 零宽字符移除
       │     - 空白折叠
       │     - 可选的全局 lowercase
       │
       ├── 3. 实体识别 (_entity.py)
       │     - spaCy NER（中文/英文模型）
       │     - 规则基 fallback（引号、大写首字母、文件路径、代码标识符...）
       │
       ├── 4. BM25 分词 (_lexical.py)
       │     - 中文：jieba 分词
       │     - 英文：spaCy lemma → stem fallback
       │     - 输出：terms 列表 + bm25_text
       │
       └── 5. 内容哈希 (_hashing.py)
             - MD5(content_hash_algorithm)

   preprocess_query(query)  # 轻量版：不做实体识别

.. admonition:: 代码洞察

   **SparseVectorEncoder** 使用 hash-trick 将 BM25 词条映射到稀疏向量：

   .. code-block:: python

       # 核心逻辑
       indices = [hash(term) % hash_dim for term in terms]  # 2M 维 hash 空间
       values  = bm25_weight(term_freq, doc_freq, doc_len, avg_doc_len)

       # 当 corpus stats 不可用时使用 TF fallback
       if not corpus_stats:
           values = log(1 + term_freq)

   这是 BM25 做稀疏检索的工程化标准方案。2M hash 维度足以覆盖绝大部分词汇，
   远小于精确词汇表的维度，且不需要维护词表状态。

2.5.2 components/chunker/ — 对话结构化
----------------------------------------

.. code-block:: text

   TurnGrouper.group(messages):
       │ 按 role 变化 + 时间间隔（time_gap_threshold）分组为 Turn
       │ 每个 turn 标记 boundary 类型

   ChunkPlanner.plan(turns):
       │ 贪心算法：将 turn 依次加入当前 chunk
       │ 直到 token 预算超限 → 新 chunk
       │ 硬预算（chunk_hard_token_budget）= 32000
       │ 软预算（chunk_soft_token_budget）= 26000

   LongTurnCompactor:
       │ 单 turn 超过 turn_hard_token_budget(16000) 时触发
       │ 策略：head(4000 tokens) + summary(LLM) + tail(4000 tokens)
       │ head 和 tail 保留为可提取证据
       │ middle summary 列为不可提取的上下文

   HistoryPacker:
       │ 跨 chunk 的向后滑动窗口
       │ 最少保留 1 个完整 turn，不超过 history_hard_token_budget(4000)

2.5.3 components/extractor/ — 记忆提取
---------------------------------------

这是核心 AI 组件，分 vanilla 和 schema 两条路径。

**Vanilla 路径（默认）**

.. code-block:: python

   class AddCoreBuilder:
       """纯编排器，接受所有依赖注入"""

       async def build(inp, context, consistency, config):
           # Phase 1: Chunking
           turns = TurnGrouper.group(messages)
           chunks = ChunkPlanner.plan(turns)

           # Phase 2: 预处理 + 召回 + 提取 (per chunk)
           for chunk in chunks:
               preprocessed = await preprocess_many(chunk.extractable_messages)
               recall = await RelatedMemoryRecall.recall(context, preprocessed)
               envelope = build_envelope(chunk, preprocessed, recall)
               extraction = await VanillaMemoryExtractor.extract(envelope)

           # Phase 3: Batch 去重
           deduped = CandidateDeduplicator.dedup(all_extractions)

           # Phase 4: Safety Gate
           actions = [AddSafetyGate.gate(candidate) for candidate in deduped]

           # Phase 5: 向量化
           vectors = await MemoryVectorizer.vectorize(actions)

           # Phase 6: 写入
           return MemoryDbWritePlan(memories, entities, vectors, relationships)

**VanillaMemoryExtractor 设计**

.. code-block:: python

   class VanillaMemoryExtractor:
       async def extract_from_envelope(envelope, preprocessed_texts, context):
           if llm_client is None:
               return self._envelope_fallback(envelope, preprocessed_texts)

           try:
               messages = _envelope_prompt_messages(envelope, preprocessed_texts)
               response = await llm_client.chat(
                   task="memory.add.extract",
                   messages=messages,
                   format_parser=parse_memory_extraction_json,
               )
               result = MemoryExtractionResult.model_validate(response.parsed)
               return _mark_extractor(result, "vanilla_llm_chunked")
           except Exception:
               return self._envelope_fallback(envelope, preprocessed_texts)  # 降级

.. admonition:: 架构决策

   **降级策略**

   如果 LLM 调用失败（网络超时、JSON 解析错误、模型不可用），
   VanillaMemoryExtractor 不会让整个请求失败。它降级到 deterministic fallback：
   - 将每条可提取消息直接作为 "fact" 写入
   - 置信度根据 chunk boundary 设置（complete=1.0, orphan=0.5）

   这保证了**可用性优先于精度**——系统在降级模式下仍然工作，只是记忆质量降低。

**Safety Gate 设计**

.. code-block:: python

   class AddSafetyGate:
       def gate_segment(self, preprocessed, mem_type, action_hint, confidence, ...):
           if len(preprocessed.normalized_text) < min_content_chars:
               return SKIP
           if action_hint == "update" and confidence < min_update_confidence:
               return ADD  # 降级到 ADD
           if action_hint == "merge" and confidence < min_merge_confidence:
               return ADD  # 降级到 ADD
           # 安全通过

.. admonition:: 代码洞察

   Safety Gate 是**最后一道防线**。LLM 可能幻觉出需要 merge/update 的高置信度
   决策，但 confidence 阈值防止了危险的写入操作。这个设计体现了 "trust but verify"
   的安全哲学。

**Schema 路径（高阶）**

Schema 模式将记忆提取为结构化的实体-属性-时间线模型：

.. code-block:: text

   SchemaExtractor:
       │ 对每个 chunk：
       │ 1. SchemaSelection — 判断是否属于已知 Schema
       │ 2. PropertyExtraction — 提取实体的属性值
       │ 3. EntityMerge & Dedup
       │ 4. Timeline Management (HAS_PROPERTY_MEMORY / NEXT_IN_PROPERTY_TIMELINE)
       │
   SchemaNormalizer:
       │ 属性归一化（别名合并、类型推断）
       │
   SchemaPlanner:
       │ 生成写入计划（创建/更新/合并实体属性和时间线边）

   Schema 处理是异步的：写入 SchemaAddBuffer → Kafka 消费者 drain → 全量推理

2.5.4 components/searcher/ — 检索组件
---------------------------------------

支持多通道召回和融合：

.. code-block:: python

   # 多通道召回
   candidates = []
   candidates += semantic_search(query, top_k=recall_size * hybrid_prefetch_factor)
   candidates += bm25_search(query, top_k=recall_size * hybrid_prefetch_factor)

   # 实体扩展
   entities = extract_entities(candidates)
   graph_candidates = traverse_neo4j(entities, rel_types=["MENTIONS", "RELATES_TO"])
   candidates += graph_candidates

   # RRF 融合
   fused = reciprocal_rank_fusion(candidates, weights={
       "semantic": 1.5,
       "bm25": 1.0,
       "entity": 1.2,
       "recent": 0.5,
   })

   # 重排序（可选）
   if use_reranker:
       fused = cohere_rerank(query, fused)

   # 最终过滤
   return apply_top_k_and_dedup(fused)

.. admonition:: 代码洞察

   **RRF（Reciprocal Rank Fusion）权重**

   不同通道的权重在 ``VanillaAddRecallConfig`` 中配置。Semantic 通道权重 1.5
   高于 BM25 的 1.0，反映了系统更信任语义匹配。Entity 通道权重 1.2 表明
   实体重叠是比纯文本 BM25 更强的召回信号。

2.5.5 components/dreaming/ — 离线记忆演化
-------------------------------------------

Dreaming Pipeline 是 MindMemOS 的"睡眠学习"机制：

.. code-block:: text

   1. Activity Collector → 读取最近 N 天的 add_record
   2. Scope Selection → 找出 "hot" 的记忆簇
      - 通过 NeighborScope 图遍历找到语义相邻的记忆组
      - 评分 = (记忆数 + 20) + (有邻居？+10)
   3. Exact-Dup 预归档 → 完全相同的 content_hash 直接归档
   4. LLM #1: Relation Detection → 检测问题类型
      (duplicate / conflict / stale / low_value / canonical / complementary)
   5. LLM #2: Action Planning → 生成操作
      (create / update / merge / archive / link)
   6. 执行 MutationPlan

.. admonition:: 代码洞察

   每次 Dreaming 运行涉及 2 次 LLM 调用 × scope 数。50 scopes × 2 calls ×
   ~2s = ~3.4 分钟。并发度 ``concurrency=8`` 可以并行处理多个 scope。
   这是典型的 "offline batch processing" 模式，适合在 Agent 空闲时（如深夜）触发。

2.5.6 components/feedback/ — 在线学习
---------------------------------------

Feedback Pipeline 实现从用户交互中学习的闭环：

.. code-block:: text

   ExplicitFeedback:
       用户主动反馈 → LLM 分析对话和召回的记忆 → 生成 add/update/delete/noop

   ImplicitFeedback:
       操作记录 → QueryRewriter 提取查询
       → RoundsCollector 收集紧凑轮次
       → SignalDetector 检测负反馈信号
         (重复请求、"不是X而是Y"、不满表达)
       → ImplicitFeedbackPlanner 生成修正动作

2.5.7 components/skill/ — Skill 系统
--------------------------------------

MindMemOS 的 Skill 系统是一个轻量级的版本管理系统：

.. code-block:: text

   Skill 版本模型类似 Git:
   - cloud_skill_id = "repo"     (SKILL.md 名称)
   - content_hash   = "tree"     (内容 SHA-256)
   - version_id     = "commit"   (确定性主键)
   - version_label  = "tag"      (显示标签)
   - parent_version_id = 父版本 (祖先链)

   状态机: observed → draft → evaluating → published → superseded
                                               └→ rolled_back

2.6 pipelines/ — Pipeline 编排层
===================================

2.6.1 Pipeline 注册表
----------------------

``pipelines/registry.py`` 维护名称→实现的映射：

.. code-block:: python

   _PIPELINE_REGISTRY: dict[str, dict[str, type]] = {
       "add": {"default_add": DefaultAddPipeline, "vanilla_add": VanillaAddPipeline},
       "search": {"default": DefaultSearchEngine, "vanilla": VanillaSearchEngine, ...},
       "dreaming": {"default_dreaming": DefaultDreamingPipeline},
       "feedback": {"default_feedback": DefaultFeedbackPipeline},
       ...
   }

2.6.2 Add Pipeline 家族
------------------------

.. list-table:: Add Pipeline 家族
   :header-rows: 1

   * - Pipeline
     - 职责
   * - DefaultAdd
     - 直写路径：文本预处理→稀疏向量→写入。无 LLM 调用
   * - VanillaAdd
     - 6 阶段全量路径：Chunking→Preprocess→Recall→Extract→
       SafetyGate→Vectorize。含 LLM 语义提取
   * - SchemaAdd
     - Schema 模式：实体-属性-时间线提取→写入 SchemaAddBuffer
       → Kafka worker drain → 全量图推理

2.6.3 Search Pipeline 家族
--------------------------

.. list-table:: Search Pipeline 家族
   :header-rows: 1

   * - Engine
     - 检索策略
   * - Default
     - 纯 BM25 稀疏检索
   * - Vanilla
     - Dense + Sparse + Graph 多通道 → RRF 融合 → ReRank
   * - Schema
     - Schema-aware: Entity → Property → Edge 三级检索
   * - Agentic
     - LLM 驱动的多轮推理：Planner → ToolRouter → Sufficiency

2.6.4 MemoryDbReader / MemoryDbWriter
--------------------------------------

这是 Pipeline 和数据库之间的 I/O 桥梁：

.. code-block:: text

   class MemoryDbReader:
       search_sparse(context, query, indices, values) → MemoryDbSearchResult
       search_dense(context, query, vector)
       search_rrf(context, query, dense_vector, sparse_vector)
       search_graph(context, query, ...)        # Neo4j 图遍历
       get_memories(context, memory_ids)         # 批量读取
       list_memory_neighbor_scopes(context, ...) # 图邻居范围列举

   class MemoryDbWriter:
       apply_mutation_plan(context, plan, consistency) → MemoryDbWriteResult
       # 内部按顺序：
       # 1. Qdrant memory upserts
       # 2. Qdrant entity upserts
       # 3. Qdrant source upserts
       # 4. Neo4j node upserts
       # 5. Neo4j relationship upserts
       # 6. Qdrant memory updates/deletes
       # 7. Neo4j node updates/deletes
       # 8. Record add_record/search_record

2.6.5 Agentic Search — 多轮推理
--------------------------------

.. code-block:: text

   AgenticSearchPipeline (wrapper)
       │
       AgenticSearchLoop
       │   Round 1:
       │   ├── Planner: "用户问的是咖啡偏好，用 vanilla search 找"
       │   ├── ToolRouter → VanillaSearchEngine
       │   ├── Sufficiency: "结果不够，只找到了一条模糊记忆"
       │   │
       │   Round 2:
       │   ├── Planner: "扩大搜索范围，用 graph recall"
       │   ├── ToolRouter → Neo4j entity expansion
       │   ├── Sufficiency: "找到了！用户说更喜欢拿铁"
       │   │
       │   Round 3 (可选):
       │   Sufficiency → "足够" → 汇总结果返回

Agentic 搜索适合需要多步推理的复杂查询（"找上周那个喜欢冰美式的用户的项目偏好"），
但额外 LLM 调用增加了延迟（2-8s vs 50-100ms）。

2.7 api/ — 路由层
==================

FastAPI 应用通过工厂函数构建：

.. code-block:: python

   app = FastAPI(title="MindMemOS API", version="0.1.0", lifespan=lifespan)
   app.include_router(memory_router)     # POST /v1/memory/add, search, get, delete, update, feedback, dreaming
   app.include_router(skill_router)       # POST /v1/skills/register, get, list, evolve, sync
   app.include_router(internal_router)    # 内部运维端点

认证系统支持四种方式，由 ``api/auth/registry.py`` 统一管理：

.. code-block:: text

   AuthRegistry
   ├── APIKeyAuth       — Bearer token → 查 api_keys.yaml
   ├── GatewayJWTAuth   — 网关签发的 JWT
   ├── InternalTokenAuth— 内部服务间共享密钥
   └── ChainedAuth      — 组合多种方式

每个认证方式实现 :

.. code-block:: python

   class AuthHandler(Protocol):
       async def authenticate(self, request: Request) -> AuthContext: ...

``AuthContext`` 包含 ``account_id``、``project_id``、``scopes`` 等信息，
通过 ``api/deps.py`` 注入到每个路由的依赖中。

2.8 workers/ — Kafka 消费者
============================

Kafka worker 架构将耗时操作异步化：

.. list-table:: Worker 设计
   :header-rows: 1

   * - Worker
     - 消费 Topic
     - 处理逻辑
     - 一致性要求
   * - MemoryAddWorker
     - ``memory.add``
     - 反序列化 context + input → 重建 RequestContext → 调用
       ``VanillaAddPipeline.add_sync()``
     - at-least-once（幂等通过 dedup_metadata_key）
   * - MemoryDreamingWorker
     - ``memory.dreaming``
     - 离线巩固执行
     - at-least-once
   * - MemoryFeedbackWorker
     - ``memory.feedback``
     - 反馈处理
     - at-least-once
   * - SchemaAddDrainWorker
     - ``schema.add.drain``
     - Schema 缓冲区 drain → 全量属性推理
     - at-least-once
   * - SchemaAddEpisodeWorker
     - ``schema.add.episode``
     - 情节边创建
     - at-least-once
   * - SkillEvolveWorker
     - ``skill.evolve``
     - 从轨迹摘要→Skill 版本演化
     - at-least-once

.. admonition:: 代码洞察

   **幂等性设计**

   Kafka 的 at-least-once 语义意味着同一条消息可能被处理多次。MindMemOS 通过
   ``dedup_metadata_key`` 实现幂等：每个 update 命令携带一个去重键，
   DB writer 在执行前检查该键是否已被处理。

2.9 mappers/ — 类型转换桥梁
============================

这是系统中最容易被忽视但最关键的一层：

.. code-block:: text

   mappers/
   ├── api.py            # FastAPI Schema → Pipeline Input DTO
   │     - Pydantic model_validate → AddPipelineInput / SearchPipelineInput
   │
   ├── db.py             # Pipeline DTO → Qdrant Point + Neo4j Statement
   │     - MemoryWrite → MemoryPoint (Qdrant PointStruct)
   │     - MemoryWrite → MemoryNode (Neo4j node)
   │     - GraphRelationship → Neo4j MERGE statment
   │
   ├── search_filters.py # DSL dict → SearchFilter → Qdrant Filter
   │     - parse_search_dsl() 用户 JSON → SearchFilter
   │     - to_qdrant_filter() SearchFilter → qmodels.Filter
   │
   ├── result.py          # QdrantRecord → MemoryView
   │
   ├── errors.py          # 异常类型转换
   │
   └── skill.py           # Skill DTO 互转

.. admonition:: 架构决策

   **为什么不在 typing/ 层直接实现 to_qdrant() 方法？**

   因为 typing/ 的 "零外部依赖" 原则禁止 import qdrant_client。如果
   MemoryWrite 有一个 to_qdrant_point() 方法，SDK 用户就必须安装 qdrant_client
   才能创建 MemoryWrite。Mapper 层作为"高依赖层"，承担所有与外部系统的对接。

============================================
第三部分：关键设计权衡与反思
============================================

3.1 为什么不用纯 RAG？
======================

简单 RAG 方案（Embedding + Top-K 检索）无法处理：

1. **版本演化**：用户的偏好从"冰美式"变成"拿铁"——RAG 只能返回两条
   互相矛盾的记忆，不知道哪条才是最新的。

2. **跨 Agent 迁移**：Agent A 积累的记忆如何迁移到 Agent B？RAG 没有
   记忆序列化协议。

3. **知识蒸馏**：100 条碎片化的工具调用日志 → 1 条精炼的"如何配置
   CI/CD"经验。RAG 做不到这种抽象。

MindMemOS 通过在 Vanilla 模式上叠加 Schema（属性时间线）和 Dreaming
（蒸馏合并）来弥补这些 gap。

3.2 架构复杂度值不值？
======================

.. list-table:: 权衡分析
   :header-rows: 1

   * - 维度
     - 收益
     - 代价
   * - 分层严格
     - 每个模块可独立测试和替换
     - 数据流动需要经过多层 DTO 转换
   * - 双存储引擎
     - 向量 + 图遍历两不误
     - 写入一致性维护复杂
   * - 离线 Dreaming
     - 记忆质量自动提升
     - 需要 Kafka + 额外 LLM 调用成本
   * - Schema 模式
     - 用户画像精准 vs. 碎片记忆
     - 抽取延迟增加 + 需要异步 drain

3.3 从代码中看出的后续方向
===========================

1. **文件记忆系统**：当前 file/url message 已定义但处理路径尚未完整实现
2. **Agentic Search 增强**：当前只有 Planner→Tool→Sufficiency 基架
3. **Skill 自动演化**：skill_evolve worker 已注册但功能还未充分开发
4. **跨模型兼容性**：liteLLM 层支持多供应商，但 Schema 模式仅验证了
   gpt-4.1-mini

============================================
附录 A：文件统计
============================================

.. code-block:: text

   src/mindmemos/mindmemos/
   ├── api/              ~15 文件    (路由 + 认证 + 服务)
   ├── components/       ~70 文件    (算法组件)
   │   ├── chunker/       ~8 文件
   │   ├── dreaming/      ~2 文件
   │   ├── extractor/     ~20 文件
   │   ├── feedback/      ~5 文件
   │   ├── memory_modeling/ ~5 文件
   │   ├── searcher/      ~15 文件
   │   ├── skill/         ~2 文件
   │   └── text/          ~9 文件
   ├── config/           ~25 文件    (配置 schema)
   ├── errors/           ~7 文件     (异常体系)
   ├── infra/            ~20 文件    (Qdrant, Neo4j, Kafka, Telemetry)
   ├── llm/              ~5 文件     (LLM 抽象)
   ├── logging/          ~2 文件     (日志 + 追踪)
   ├── mappers/          ~6 文件     (类型转换)
   ├── pipelines/        ~30 文件    (Pipeline 编排)
   ├── prompts/          ~25 文件    (LLM 提示词 EN + ZH)
   ├── typing/           ~7 文件     (DTO 契约)
   └── workers/          ~7 文件     (Kafka 消费者)
