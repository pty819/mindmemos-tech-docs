Add Pipeline — 记忆写入
========================

Add Pipeline 是 MindMemOS 最复杂的子系统和性能关键路径，有两条实现路径：

1. **DefaultAddPipeline** — 轻量直写路径（``default_add``）
2. **VanillaAddPipeline** — 6 阶段全量提取路径（``vanilla_add``，默认）

DefaultAddPipeline — 直写路径
------------------------------

位于 ``pipelines/add/default.py``。

**适用场景**：纯文本/文件知识入库，不需要 LLM 语义提取。

**流程**：

.. code-block:: text

   Input (TextMessage / FileMessage / UrlMessage)
       │
       ▼
   1. TextPreprocessor.preprocess_text()
      │  - 语言检测 → 归一化 → 实体识别（NER）→ tokenize → BM25
      ▼
   2. MemoryWrite 构造
      │  - 分配 memory_id (uuid4)
      │  - 构建 payload（content_hash, bm25_text, tokens, lang, ...）
      ▼
   3. SparseVectorEncoder.encode_document()
      │  - 稀疏向量编码（hash trick + BM25 权重）
      ▼
   4. Entity 管理与图边
      │  - 去重 entity → EntityWrite
      │  - 创建 MENTIONS 关系（Memory→Entity）
      ▼
   5. MemoryDbWriter.apply_mutation_plan()
      │  - Qdrant + Neo4j 写入

VanillaAddPipeline — 6 阶段全量路径
-----------------------------------

位于 ``pipelines/add/vanilla/vanilla_add.py``，编排由 ``AddCoreBuilder``（``components/extractor/vanilla/add_builder.py``）驱动。

**适用场景**：对话/工具调用轨迹的语义理解与记忆提取。

**6 阶段流程**：

.. code-block:: text

   Phase 1: 对话分割 (Chunking)
   │ 1a. TurnGrouper.group()    — 将消息流分组为对话回合 (Turn)
   │ 1b. ChunkPlanner.plan()    — 回合打包为 Chunk（控制 token 预算）
   │ 1c. LongTurnCompactor()    — 超长回合的 head+summary+tail 压缩
   │
   Phase 2: 文本预处理 (Preprocess)
   │ TextPreprocessor.preprocess_many()
   │  - 语言检测 → 归一化 → NER → tokenize → BM25 → content_hash
   │
   Phase 3: 相关记忆召回 (Recall)
   │ RelatedMemoryRecall.recall()
   │  - 稀疏向量检索 → 实体扩展 → 图邻居召回 → RRF 融合
   │
   Phase 4: LLM 提取 (Extract)
   │ VanillaMemoryExtractor.extract_from_envelope()
   │  - 构建 ExtractionEnvelope（extractable / context 分离）
   │  - LLM 调用 → 解析 JSON → 提取 MemoryCandidate + EntityCandidate
   │  - 失败时降级为 deterministic fallback
   │
   Phase 5: 候选去重与动作规划 (Dedup + Safety Gate)
   │ 5a. CandidateDeduplicator.dedup()    — 跨 chunk 去重
   │ 5b. AddSafetyGate.gate_segment()     — 确定动作类型:
   │        ADD / REINFORCE / UPDATE / MERGE / SKIP
   │
   Phase 6: 向量化与持久化 (Vectorize + Write)
   │ MemoryVectorizer.vectorize()
   │  - Sparse: BM25 hash-trick 编码
   │  - Dense: Embedding API 调用（配置 "fast" 时异步）
   │ MemoryDbWriter.apply_mutation_plan()
   │  - Qdrant 写入点 + Neo4j 创建边

Chunking 细节
~~~~~~~~~~~~~

Chunk 策略由 ``components/chunker/vanilla/`` 实现：

.. code-block:: text

   TurnGrouper     — 消息→回合（按用户意图分组）
   ChunkPlanner    — 回合→Chunk（硬 token 预算 + 软边界检测）
   LongTurnCompactor — 超标回合 → head + summary + tail
   HistoryPacker   — 跨 chunk 滑动历史窗口
   LongTurnSummarizer — LLM 压缩超长回合（附结构化摘要）

　

ExtractionEnvelope — LLM 提取上下文
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class ExtractionEnvelope(BaseModel):
       extractable_messages: list[TurnMessageRef]     # 可提取的证据
       current_context_messages: list[TurnMessageRef] # 不可提取的上下文
       history: HistoryPack                           # 历史滑动窗口
       recalled_memories: list[dict]                  # 相关记忆召回结果
       boundary: ChunkBoundary                        # 边界类型

Chunk 边界类型影响 LLM 提取的保守度：

.. list-table:: Chunk 边界类型与 LLM 指导行为
   :header-rows: 1

   * - 边界类型
     - LLM 指导行为
   * - complete
     - 全信心提取
   * - compacted
     - 仅从头尾提取，中间摘要不可提取
   * - open_head
     - 保守提取，标记为部分证据
   * - open_tail
     - 不将最后一轮视为结论
   * - orphan
     - 仅提取明确稳定的事实

Safety Gate — 动作决策
~~~~~~~~~~~~~~~~~~~~~~~

确定每个候选记忆的最终动作：

.. code-block:: python

   class PlannedAddAction(BaseModel):
       action_type: Literal["add", "reinforce", "update", "merge", "skip"]
       memory_id: str | None
       content: str | None
       reason: str

- **add** — 全新记忆
- **reinforce** — 已有记忆的强化计数 +1
- **update** — 内容修正/补充
- **merge** — 合并相关记忆并归档旧记忆
- **skip** — 低质量/重复内容跳过

Schema Add Pipeline
-------------------

对于 Schema 模式（启用实体属性建模），走 ``pipelines/add/schema/schema_add.py``：

- 通过 SchemaExtractor 提取实体的结构化属性
- 将属性建模为 HAS_PROPERTY_MEMORY 关系
- 维护实体的属性时间线（NEXT_IN_PROPERTY_TIMELINE）
- 异步 drain 流程：Kafka 消费者处理 SchemaAdd 缓冲区 → 全量推理 → 图边创建
