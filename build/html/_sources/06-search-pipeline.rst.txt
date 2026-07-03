Search Pipeline — 记忆检索
============================

Search Pipeline 架构为 **多引擎 + 可选 Agentic 编排**。各引擎统一实现 ``SearchEngine``
Protocol，由 ``DefaultSearchPipeline`` 调度。

检索引擎
--------

.. code-block:: text

   search/
   ├── pipeline.py          # DefaultSearchPipeline — 引擎调度器
   ├── default.py           # DefaultSearchEngine — BM25 稀疏检索
   ├── vanilla/
   │   └── engine.py        # VanillaSearchEngine — 多阶段检索（预过滤→向量→图→RRF→重排）
   └── schema/
       └── engine.py        # SchemaSearchEngine — Schema 模式检索（实体/属性/边）
   └── agentic/             # Agentic 多轮检索编排

DefaultSearchEngine — 纯 BM25
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

位于 ``pipelines/search/default.py``。最简单的检索方式：

.. code-block:: python

   1. TextPreprocessor.preprocess_query()  # 查询预处理
   2. SparseVectorEncoder.encode_query()   # 稀疏向量编码
   3. db_reader.search_sparse()             # Qdrant 稀疏检索
   4. 结果包装为 MemorySearchItem

VanillaSearchEngine — 多阶段检索
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

位于 ``pipelines/search/vanilla/engine.py``，通过 ``components/searcher/`` 中的子组件实现：

.. code-block:: text

   Phase 1: Query 预处理
   │  - 语言检测 → 归一化 → Tokenize → 实体识别
   │
   Phase 2: 多源候选召回
   │  ├─ Semantic: Dense向量检索（Qdrant 全精度搜索）
   │  ├─ BM25: 稀疏向量检索
   │  ├─ Graph: 实体图邻居遍历（Neo4j）
   │  └─ Schema: 属性/关系检索
   │
   Phase 3: 候选融合
   │  RRF (Reciprocal Rank Fusion) — 多源排序分融合
   │
   Phase 4: 实体扩展
   │  EntityRecall — 从候选记忆提取实体 → 再次图遍历扩召
   │
   Phase 5: 重排序
   │  Reranker — 交叉编码器（cohere rerank 等）
   │
   Phase 6: 最终过滤
   │  FinalFilter — 应用 filter + top_k + 去重

SchemaSearchEngine — 结构化检索
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

位于 ``pipelines/search/schema/engine.py``：

- 通过 ``entity_recall.py`` 和 ``property_recall.py`` 检索实体属性
- ``SchemaSearchExpander`` 扩召到相关实体
- ``_ranker.py`` 和 ``_entity_fusion.py`` 融合实体粒度的排序分

Agentic Search — 多轮推理检索
~~~~~~~~~~~~~~~~~~~~~~~~~~~~～

位于 ``pipelines/search/agentic/``：

.. code-block:: text

   AgenticSearchPipeline (wrapper)
       │
       ▼
   AgenticSearchLoop
       │  每轮：
       │  1. Planner — 生成检索计划
       │  2. ToolRouter — 选择引擎（vanilla/schema/graph/get）
       │  3. Engine 执行 → 返回结果
       │  4. Sufficiency — 判定是否足够
       │  5. 不足则 Planner 调整 → 下一轮
       │
       ▼
   最终 Sufficiency → 汇总结果

Agentic 模式适合需要多步推理的复杂查询（如"帮我找找上周提过那个喜欢冰美式的用户项目偏好相关的事情"）。

检索后端 — MemoryDbReader
--------------------------

位于 ``pipelines/memory_db/reader.py``，封装 Qdrant 和 Neo4j 的读取操作：

.. code-block:: text

   search_sparse()        — BM25 稀疏检索
   search_dense()         — 全精度 Dense 检索
   search_hybrid()        — 稀疏+稠密混合
   search_rrf()           — RRF 融合检索
   search_graph()         — 图邻居遍历检索
   get_memories()         — 按 memory_id 批量获取
   list_memory_neighbor_scopes() — 图邻居范围列举

过滤系统
--------

用户请求中的 ``filters`` 参数经过以下链路：

.. code-block:: text

   用户 DSL (JSON) → mappers/memory/parse_search_dsl() → SearchFilter
       │
       ▼
   注入 project_id → 强制隔离
   注入 status=active → 仅返回活跃记忆
       │
       ▼
   SearchFilter → MemoryDbSearchQuery → Qdrant FieldCondition
