# MindMemOS — 模块设计解析

> 全量源码分析，分为两篇：
> 
> **设计理念篇** — 从顶层回答"知识在系统中怎么拆解、存储、组装、提取、巩固和反馈"。
> 不涉及具体代码，只讨论设计哲学和业务模型。
>
> **技术框架篇** — 从底层回答"模块之间怎么穿起来的、调用链怎么走的"。
> 基于 `src/mindmemos/mindmemos/` 代码的具体模块依赖和调用路径。

```{toctree}
:maxdepth: 2
:caption: 上篇 · 设计理念篇
:numbered:

12-knowledge-model
13-knowledge-ingestion
14-knowledge-organization
15-knowledge-retrieval
16-knowledge-consolidation
17-knowledge-feedback
```

```{toctree}
:maxdepth: 2
:caption: 下篇 · 技术框架篇
:numbered:

01-intro
02-module-architecture
03-pipeline-registry
04-api-to-storage
05-dual-write
06-kafka-bus
07-add-chain
08-search-chain
09-dreaming-chain
10-feedback-chain
11-deployment
```
