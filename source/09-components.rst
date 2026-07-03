核心组件详解
============

``components/`` 是 MindMemOS 的算法实现层，每个子包聚焦一个职责领域。

文本处理
--------

.. code-block:: text

   components/text/
   ├── preprocessor.py   — 主入口：语言检测 → 归一化 → NER → tokenize → BM25
   ├── _language.py      — 语言检测（中/英/混合，基于 Unicode 字符比例）
   ├── _normalize.py     — 文本归一化（空格折叠、Unicode 规范化、标点清理）
   ├── _entity.py        — 规则基命名实体识别（正则+词表）
   ├── _lexical.py       — BM25 分词（支持中文 jieba + 英文空格分词）
   ├── _hashing.py       — Hash-trick 索引映射
   ├── vectorizer.py     — MemoryVectorizer（稀疏 + 稠密向量生成）
   ├── sparse.py         — SparseVectorEncoder（BM25 hash-trick 权重编码）
   └── preprocessor.py   — TextPreprocessor（上述流程编排器）

TextPreprocessor 是核心编排器，支持两个入口：

.. code-block:: python

   preprocessor.preprocess_text(text, segment_id)   # 完整路径
   preprocessor.preprocess_query(text)              # 只做查询所需的最小路径（无 NER）

向量化
~~~~~~

在 ``components/text/vectorizer.py``：

- **Sparse**：基于 BM25 hash-trick 的稀疏向量（同步）
- **Dense**：通过 Embedding API 生成稠密向量（"fast"模式异步不等待）

对话分割
--------

.. code-block:: text

   components/chunker/
   ├── segmenter.py           — SourceAwareSegment 定义
   ├── episodes_chunker.py    — 情节级分割（Schema 模式）
   └── vanilla/
       ├── turn_grouper.py    — 消息→回合（按用户意图）
       ├── chunk_planner.py   — 回合→Chunk (token 预算控制)
       ├── compactor.py       — 超长回合压缩
       ├── history_packer.py  — 跨 chunk 历史滑动窗口
       └── summarizer.py      — LLM 回合摘要
       └── chunk_planner.py   — 含边界类型判定

ChunkPlanner 通过硬 token 预算和软策略避免在 LLM 调用上浪费 token。

记忆抽取
--------

.. code-block:: text

   components/extractor/
   ├── protocols.py          — 抽取器 Protocol 定义
   ├── vanilla/              — 默认抽取路径
   │   ├── add_builder.py    — 6 阶段编排（AddCoreBuilder）
   │   ├── add_recall.py     — RelatedMemoryRecall（召回相关记忆）
   │   ├── memory.py         — VanillaMemoryExtractor（主 LLM 调用）
   │   ├── _dedup.py         — 跨 chunk 候选去重
   │   ├── _entity.py        — 实体解析和去重
   │   ├── _safety_gate.py   — 动作规划（ADD/REINFORCE/UPDATE/MERGE/SKIP）
   │   └── _update_commands.py — 合并/归档/更新/强化命令构建
   └── schema/               — Schema 模式抽取
       ├── schema_extractor.py  — SchemaExtractor（实体属性提取）
       ├── schema_normalizer.py — 属性归一化（别名合并、类型推断）
       ├── schema_planner.py    — Schema 写入规划
       ├── base.py              — Schema 基础类型
       └── ...                  — 高阶属性、合并策略、搜索字段

记忆建模
--------

.. code-block:: text

   components/memory_modeling/
   ├── vanilla/
   │   └── edges.py                   — 基础图边构建（MENTIONS, EXTRACTED_FROM）
   └── schema/
       ├── base.py                    — Schema 模型基类
       ├── edge.py                    — Schema 图边（HAS_PROPERTY_MEMORY, NEXT_IN_PROPERTY_TIMELINE）
       ├── entity_manager.py          — Schema 实体管理器（属性查询/更新）
       └── temporal_entity.py         — 时间线实体模型

搜索组件
--------

.. code-block:: text

   components/searcher/
   ├── protocols.py       — 召回策略协议
   ├── final_filter.py    — 最终过滤（去重 + top_k + 排序）
   ├── entity_recall.py   — 实体扩展召回（从候选记忆→实体→更多记忆）
   ├── rerank.py          — 交叉编码器重排序
   ├── rrf.py             — RRF 融合排序
   └── schema/            — Schema 模式检索
       ├── schema_search_expander.py — Schema 搜索扩召
       ├── property_recall.py        — 属性层面检索
       ├── _ranker.py                — Schema 排序器
       ├── _entity_weights.py        — 实体权重计算
       ├── _entity_fusion.py         — 实体融合
       ├── _entity_shrink.py         — 实体压缩
       └── _query_builder.py         — Schema 查询构建

Dreaming 组件
~~~~~~~~~~~~~

.. code-block:: text

   components/dreaming/
   ├── relation_detection.py  — LLM 驱动的问题检测（重复/冲突/过时/低价值）
   └── action_planning.py     — LLM 驱动的合并/更新/归档决策

活动记录
--------

``components/activity/collector.py`` 中的 ``RecentActivityCollector`` 负责从
Qdrant add\_record 集合中提取最近写入活动，供 Dreaming Pipeline 选择 hot scopes。
