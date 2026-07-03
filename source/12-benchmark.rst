Benchmark 与性能
================

LoCoMo 基准 — 对话记忆
-----------------------

LoCoMo（Long-term Conversation Memory）是最主流的长期记忆评估基准。
MindMemOS-schema 在此基准上取得 **Overall 93.64** 的 SOTA 成绩。

.. list-table:: LoCoMo 完整对比
   :header-rows: 1

   * - 方法
     - Single Hop
     - Multi Hop
     - Temporal
     - Open Domain
     - **Overall**
   * - **MindMemOS-schema**
     - **97.62**
     - **93.26**
     - 89.01
     - 75.00
     - **93.64**
   * - EverMemOS
     - 96.67
     - 91.84
     - **89.72**
     - **76.04**
     - 93.05
   * - Zep
     - 90.84
     - 81.91
     - 77.26
     - 75.00
     - 85.22
   * - MemOS
     - 85.37
     - 79.43
     - 75.08
     - 64.58
     - 80.76
   * - MemU
     - 74.91
     - 72.34
     - 43.61
     - 54.17
     - 66.67
   * - Mem0
     - 68.97
     - 61.70
     - 58.26
     - 50.00
     - 64.20
   * - MemoryOS
     - 67.30
     - 59.34
     - 42.26
     - 59.03
     - 60.11

**测试条件**：回复模型 = gpt-4.1-mini；Baseline 数据引用自 EverMemOS 论文。

要点分析
~~~~~~~~

- **Single Hop（97.62）**：单一事实点检索近乎完美
- **Multi Hop（93.26）**：多跳推理超越 EverMemOS，归功于 Schema 的实体属性建模和图遍历
- **Temporal（89.01）**：略低于 EverMemOS，时序推理仍有优化空间
- **Open Domain（75.00）**：开放域检索持平，说明通用知识召回能力接近瓶颈

PersonaMem 基准 — 用户画像
---------------------------

.. list-table:: PersonaMem 对比
   :header-rows: 1

   * - 方法
     - Recall Sha.
     - Recall Men.
     - Track Evo.
     - Revisit
     - Suggest
     - Recommend
     - Generalize
     - **Overall**
   * - **MindMemOS**
     - 73.64%
     - **82.35%**
     - **67.63%**
     - 85.86%
     - 35.48%
     - **80.00%**
     - 78.95%
     - **69.61%**
   * - EverMemOS
     - 74.42%
     - 64.71%
     - 64.03%
     - 85.86%
     - 35.48%
     - 65.45%
     - 84.21%
     - 67.57%

**Recall Men. (Ack. Latest)** 和 **Recommend** 指标大幅领先，源于 Schema 模式对
用户偏好的结构化建模能力。

Dreaming 效果
-------------

MemoryAgentBench 基准评测 Dreaming 前后的效果变化：

.. list-table:: Dreaming 有效性
   :header-rows: 1

   * - 指标
     - 开启前
     - 开启后
     - 变化
   * - Single-hop SubEM
     - 83.00%
     - 88.75%
     - **+5.75%** 🟢
   * - Multi-hop SubEM
     - 10.75%
     - 14.00%
     - **+3.25%** 🟢
   * - 记忆数量变化
     - (baseline)
     - -27.9%
     - **减少 28%** 🟢

Dreaming 通过合并重复、归档低价值、链接相关记忆，在提升检索精度的同时**减少了**记忆总量。

性能特征
--------

- **API 延迟**：sync add ~2-5s（含 LLM 调用），async add ~50ms（仅 Kafka 入队）
- **检索延迟**：fast 模式 ~50-100ms，agentic 模式 ~2-8s（多轮推理）
- **Dreaming 吞吐**：每 scope ~2-4 秒（含 2 次 LLM 调用），50 scopes 约 2-3 分钟
