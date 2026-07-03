Dreaming Pipeline — 离线记忆巩固
================================

Dreaming（"梦境"）是 MindMemOS 的**离线记忆演化机制**，自动检测记忆热点区域，
通过 LLM 驱动的分析和决策来合并、更新、归档记忆。

整体流程
--------

.. code-block:: text

   Activity Collector
       │ 收集近期的 written_memories（按 days.lookback_days）
       ▼
   Scope Selection (阶段1)
       │ 选出 hot scopes（通过图邻居遍历确定聚类范围）
       ▼
   Cluster Dedup (阶段2)
       │ 去除重复 scope（按记忆 ID 集合去重）
       ▼
   For each scope (阶段3):
       │  ├─ Exact-Dup 预归档（content_hash 完全重复）
       │  ├─ LLM #1: Relation Detection
       │  │   检测聚类中的问题：重复、冲突、低价值、过时等
       │  │
       │  └─ LLM #2: Action Planning (每组问题)
       │      生成合并/更新/归档/新创建等操作
       │
       ▼
   Action Application (阶段4)
       申请 MemoryDbMutationPlan → db_writer 执行

作用机制
--------

关键数据结构
~~~~~~~~~~~~

.. code-block:: python

   @dataclass(frozen=True)
   class ConsolidationScope:
       entity_id: str | None
       property_name: str | None
       root_id: str | None
       score: int                     # 热度评分
       seed_memory_ids: tuple[str, ...]
       add_record_ids: tuple[str, ...]

   class ConsolidationAction(BaseModel):
       creates: list[ConsolidationCreate]     # 新记忆
       updates: list[ConsolidationUpdate]     # 更新（quality_signal）
       merges: list[ConsolidationMerge]       # 合并（归档源 + 创建目标）
       archives: list[ConsolidationArchive]   # 归档
       links: list[ConsolidationLink]         # 图关系

LLM 调用阶段
~~~~~~~~~~~~

**LLM #1 — Relation Detection**  (``components/dreaming/relation_detection.py``)：

输入：一个聚类内的所有记忆 + scope 元信息
输出：``DetectedMemoryIssueGroup`` — 问题类型包括：

- ``duplicate`` — 内容重复
- ``conflict`` — 矛盾点
- ``stale`` — 过时信息
- ``low_value`` — 低价值/噪声
- ``canonical`` — 可以确立为标准表述
- ``complementary`` — 互补可合并

**LLM #2 — Action Planning**  (``components/dreaming/action_planning.py``)：

输入：问题组 + 相关记忆
输出：``ConsolidationAction`` — 具体的增/改/删/合操作

两种执行模式
------------

.. code-block:: text

   模式          | Sync                     | Async (默认)
   -------------+--------------------------+-------------------------
   触发方式     | HTTP 请求 ?sync=true      | Kafka 消息
   返回值       | 实时 consolidation 统计   | status=queued
   用例         | 调试/测试                 | 生产定时任务

配置项
------

在 ``config/algo/dreaming.py`` 中定义：

.. code-block:: python

   class DreamingConfig(BaseModel):
       lookback_days: int = 7            # 最近活动窗口
       max_scopes_per_run: int = 50      # 每轮处理 scope 数
       max_memories_per_scope: int = 50  # 每个 scope 最大记忆数
       min_cluster_size: int = 3         # 最小聚类规模
       min_scope_updates: int = 3        # 最小更新数
       concurrency: int = 4             # 并发处理 scope 数
       scope_batch_size: int = 20       # 图遍历批大小
