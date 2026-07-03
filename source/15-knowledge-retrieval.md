# 知识组装排序提取设计

本章回答核心问题：**用户发起一次查询后，系统如何从海量记忆中找出最相关的知识，并按什么逻辑排序后返回？**

## 检索的三层设计

MindMemOS 的检索不是单次向量匹配，而是**三层流水线**：

```{mermaid}
graph TB
    subgraph "Layer 1: 候选生成"
        Q["用户查询"] --> BM25["BM25 稀疏向量检索<br/>→ top-k 候选"]
        Q --> ENT["Entity 入口检索<br/>→ 实体名匹配"]
        Q --> SCHEMA["Schema 扩展检索<br/>→ 属性扩展"]
    end
    
    subgraph "Layer 2: 重排序与过滤"
        BM25 --> RANK["Reranker<br/>→ 多信号排序"]
        ENT --> RANK
        SCHEMA --> RANK
        
        RANK --> FILTER["FinalFilter<br/>→ 去重 + 权限 + 状态"]
    end
    
    subgraph "Layer 3: 上下文组装"
        FILTER --> GRAPH["Neo4j 邻居扩展<br/>→ 相关上下文"]
        GRAPH --> ASSEMBLE["组装为 SearchResult<br/>→ 排序 + 来源标注"]
    end
    
    ASSEMBLE --> OUT["返回给调用方"]
```

这个三层设计的哲学：**先快后慢、先粗后精**。第一层用低代价的 BM25 缩小候选范围，第二层用多信号排序精排，第三层用图遍历补充上下文。

## 第一层：候选生成（Candidate Generation）

### BM25 稀疏检索（默认路径）

```{mermaid}
sequenceDiagram
    participant Q as 查询
    participant P as TextPreprocessor
    participant E as SparseVectorEncoder
    participant D as MemoryDbReader
    participant Qd as QdrantEngine

    Q->>P: preprocess_query(text)
    P->>P: 分词 (同写入路径一致的预处理)
    P-->>E: tokens
    
    E->>E: encode_query(tokens)
    Note over E: 使用写入时相同的 hash trick 维度
    E-->>D: SparseVector(indices, values)
    
    D->>D: 构建 MemoryDbSearchQuery
    Note over D: query.tokens + top_k + filters(project_id + status)
    
    D->>Qd: search_memories(project_id, vector, filter)
    Note over Qd: Qdrant sparse search with payload filter
    Qd-->>D: QdrantSearchResult(hits)
    
    D-->>Q: MemorySearchItem[](id, content, type, timestamp)
```

**设计要点**：
- 查询的预处理路径必须和写入路径**完全一致**——同样的分词器、同样的 hash 维度、同样的 IDF 统计
- 查询时 BMI25 的 IDF 权重来自写入时的全局语料统计（CorpusStats）
- 默认 top_k 可配置，通常为 20-50

### Entity 入口检索

不是所有查询都通过 BM25 进入。如果查询明确提到一个已知实体，可以直接通过实体入口找到相关 Memory：

```{mermaid}
graph LR
    Q["用户: 'MFC-1 的情况'"] -->|"实体识别"| ENT["Entity: MFC-1"]
    ENT -->|"Neo4j 遍历"| M["所有 MENTIONS MFC-1 的 Memory"]
    M -->|"按时间排序"| R["返回结果"]
```

### Schema 扩展检索

当查询涉及实体的属性时，Schema Search Engine 会：

1. 先用 BM25 找到涉及相关实体的候选 Memory
2. 通过 `property_recall` 拉取这些实体的属性值
3. 用 `entity_fusion` 合并多源属性
4. 返回实体属性作为增强上下文

## 第二层：重排序与过滤（Ranking & Filtering）

候选集生成后，系统不直接返回——需要经过重排序和过滤。

### 排序信号

MindMemOS 使用**多信号排序**，不是单一 BM25 score：

```{mermaid}
graph LR
    subgraph "排序信号"
        S1["BM25 score<br/>词汇重叠度"]
        S2["时效性<br/>created_at"]
        S3["强化次数<br/>reinforcement_count"]
        S4["实体匹配度<br/>entity overlap"]
        S5["人工评分<br/>feedback score"]
    end
    
    S1 --> R["Reranker"]
    S2 --> R
    S3 --> R
    S4 --> R
    S5 --> R
    
    R --> OUT["最终排序"]
```

每个信号有权重配置，可在 API key 级别的 `override_config` 中调整。

### 过滤器链

排序后的候选集经过三层过滤：

```
Layer 1: 硬过滤（存储层）
  - project_id = 当前请求的 project
  - status = active（已归档的不返回）

Layer 2: 语义过滤（应用层）
  - entity_relevance_filter: 保留和查询实体相关的
  - dedup: 去除重复内容（基于 content_hash）

Layer 3: 业务过滤（DSL）
  - 用户通过 filters 参数传递的条件
  - 支持: must / should / must_not 逻辑组合
  - 过滤字段: mem_type, timestamp, metadata.*
```

## 第三层：上下文组装

排序过滤后的候选集可以进一步**通过图遍历补充相关上下文**：

```{mermaid}
sequenceDiagram
    participant P as SearchPipeline
    participant D as MemoryDbReader
    participant Q as Qdrant
    participant N as Neo4j

    P->>D: search_sparse() → 50 candidates
    
    P->>P: rerank + filter → top 10
    
    P->>D: list_memory_neighbor_scopes(top 10 IDs)
    D->>N: shared_entity 遍历
    Note over N: MATCH (seed:Memory)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(related:Memory)
    N-->>D: GraphNeighborScope[] (共享实体的其他 Memory)
    
    P->>P: 将邻居 Memory 附加到结果中
    Note over P: 用户看到的不只是直接匹配，还有"相关的"
    
    P-->>CALLER: SearchPipelineResult
```

**设计取舍**：图扩展是可选的（受配置控制），默认不开启。只有 Agentic Search 和 Schema Search 默认执行图扩展。

## 四种检索策略的适用场景

MindMemOS 支持四种检索策略，通过 `memory_algorithm` 选择：

| 策略 | 引擎 | 候选生成 | 排序 | 适用场景 |
|------|------|---------|------|---------|
| `default` | BM25 | 全文稀疏检索 | BM25 score | 通用知识检索 |
| `vanilla` | BM25 + Entity | 全文 + 实体入口 | 多信号 | 需要实体感知的检索 |
| `schema` | BM25 + Schema | 全文 + 属性扩展 | 多信号 + 实体融合 | 需要结构知识的检索 |
| `agentic` | LLM 驱动 | 多轮决策搜索 | LLM 评估 | 复杂多跳推理 |

```{mermaid}
graph TB
    QUERY["用户查询"] --> R{"检索策略?"}
    
    R -->|"default"| D["BM25 全量检索<br/>→ 候选 → 排序 → 返回"]
    R -->|"vanilla"| V["BM25 + 实体入口<br/>→ 实体匹配增强"]
    R -->|"schema"| S["BM25 + 属性扩展<br/>→ 实体融合 + 属性召回"]
    R -->|"agentic"| A["LLM 多轮搜索<br/>→ 规划 → 执行 → 评估"]
    
    D -->|"100ms"| OUT["响应时间"]
    V -->|"200ms"| OUT
    S -->|"500ms"| OUT
    A -->|"2-5s"| OUT
```

## 检索结果的生命周期

搜索结果不仅返回给用户，还会被记录到 `search_record_v1` 集合中，供后续的 **Feedback Pipeline** 使用：

```
搜索结果 → 记录为 SearchActivityEvent
         → 包含: query, recalled_memories, scores
         → 后续 Feedback 可以强化/弱化基于这些结果
```

这就是**检索-反馈闭环**的基础——每次检索都在为系统提供学习信号。

## 检索设计原则总结

| 原则 | 含义 |
|------|------|
| **先快后慢** | BM25 先出候选，再逐步精排 |
| **多信号排序** | 不依赖单一 BM25 score |
| **可扩展上下文** | 图遍历补充相关记忆 |
| **检索即学习** | 每次检索被记录，形成反馈信号 |
| **策略可配置** | 4 种策略适应不同场景 |
| **查询-写入同路径** | 预处理路径完全一致，保证可复现性 |
