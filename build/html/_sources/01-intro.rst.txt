项目概述
========

MindMemOS 是由 **mindscale-noah** 组织开发的开源长期记忆系统（MIT 协议），专为 AI Agent
和 LLM 应用设计。其核心理念是：**让 Agent 具备可演化、跨会话、跨 Agent 迁移的持久记忆**。

定位与目标
----------

- **对标产品**：Mem0、Zep、MemOS、EverMemOS
- **覆盖范围**：对话记忆、用户画像（Persona）、文件知识、工具调用轨迹
- **差异化**：Schema-based 实体属性建模 + 离线 Dreaming 自动演化 + Skills 自动沉淀

项目现状
--------

- **语言**：Python >= 3.11，uv 工作空间管理
- **架构**：FastAPI 后端 + Qdrant（向量库）+ Neo4j（图库）+ Kafka（事件流）
- **发布**：PyPI 包 ``mindmemos-sdk``，npm 包 ``@mindmemos/openclaw-plugin``
- **Stars**：55 | **Forks**：3 | **License**：MIT
- **代码规模**：~190 个 Python 源文件，覆盖 6 大核心子系统

关键 Benchmark
--------------

.. list-table:: LoCoMo 对话记忆基准
   :header-rows: 1

   * - 方法
     - Single Hop
     - Multi Hop
     - Temporal
     - Open Domain
     - **Overall**
   * - MindMemOS-schema
     - **97.62**
     - **93.26**
     - 89.01
     - 75.00
     - **93.64**
   * - EverMemOS
     - 96.67
     - 91.84
     - 89.72
     - 76.04
     - 93.05
   * - Zep
     - 90.84
     - 81.91
     - 77.26
     - 75.00
     - 85.22

本章小结
--------

MindMemOS 是一个高度工程化的记忆系统，不是简单的 key-value 存储或 RAG。
它通过六层 Pipeline 架构、双引擎检索策略和离线演化机制，在 **LoCoMo**
基准上取得了 93.64 Overall 的 SOTA 成绩。
