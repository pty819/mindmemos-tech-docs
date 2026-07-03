# Search Pipeline 模块调用链

Search Pipeline 是**读入口**。它不像 Add 那样有一条固定的执行路径——同一个 `search()` 方法会根据配置走不同引擎。

## Search Pipeline 路由

```{mermaid}
graph TB
    MS["MemoryService.search()"] --> SP["SearchPipeline<br/>pipelines/search/pipeline.py"]
    
    SP --> ROUTE{"memory_algorithm<br/>search_pipeline?"}
    
    ROUTE -->|"vanilla"| VE["VanillaSearchEngine<br/>pipelines/search/vanilla/engine.py"]
    ROUTE -->|"schema"| SE["SchemaSearchEngine<br/>pipelines/search/schema/engine.py"]
    ROUTE -->|"default"| DE["DefaultSearchEngine<br/>pipelines/search/default.py"]
    ROUTE -->|"agentic"| AE["AgenticSearchPipeline<br/>pipelines/search/agentic/pipeline.py"]
    
    VE --> VQ["BM25 Qdrant 检索"]
    DE --> DQ["BM25 Qdrant 检索"]
    SE --> SC["schema 扩展链"]
    AE --> AL["LLM 驱动的多轮搜索循环"]
```

## BM25 路径（DefaultSearchEngine）

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant P as SearchPipeline
    participant DE as DefaultSearchEngine
    participant TP as TextPreprocessor
    participant SE as SparseVectorEncoder
    participant R as MemoryDbReader
    participant Q as QdrantEngine

    SVC->>P: search(payload, context)
    P->>DE: search_candidates()
    
    DE->>TP: preprocess_query(query, include_entities=False)
    TP-->>DE: PreprocessResult(tokens, ...)
    
    DE->>SE: encode_query(tokens)
    SE-->>DE: SparseVector(indices, values)
    
    DE->>DE: 构建 MemoryDbSearchQuery(query, top_k, filters)
    
    DE->>R: search_sparse(context, query, indices, values)
    
    R->>Q: qdrant.search_memories(project_id, sparse_vector, filter)
    Note over Q: Qdrant 的稀疏 BM25 搜索
    Q-->>R: QdrantSearchResult(hits)
    
    R-->>DE: MemoryDbSearchResult
    DE-->>P: MemorySearchItem[]
    
    P-->>SVC: SearchPipelineResult
```

**代码锚点**：`pipelines/search/default.py:40-75`

```python
async def search_candidates(self, inp, context, *, options=None):
    preprocessed = self._text_preprocessor.preprocess_query(inp.query)
    sparse = self._sparse_encoder.encode_query(preprocessed.tokens)
    query = MemoryDbSearchQuery(query=inp.query, top_k=..., mode="bm25")
    result = await self.db_reader.search_sparse(context, query, indices, values)
    return [MemorySearchItem(...) for hit in result.hits]
```

## Schema Search 路径

```{mermaid}
graph TB
    SP["SearchPipeline.search()"] --> SSE["SchemaSearchEngine"]
    
    subgraph "Schema Search 扩展链"
        SSE --> SC["schema_search_expander.expand()"]
        SC --> S1["BM25 检索 candidate memories"]
        S1 --> S2["从 candidates 提取 entities"]
        S2 --> S3["property_recall()<br/>查 entity 属性"]
        S3 --> S4["entity_fusion()<br/>多源实体融合"]
        S4 --> S5["_ranker.rank()<br/>排序"]
        S5 --> S6["final_filter.filter()<br/>最终过滤"]
    end
    
    subgraph "依赖的模块"
        S3 --> PR["components/searcher/schema/property_recall.py"]
        S4 --> EF["components/searcher/schema/_entity_fusion.py"]
        S5 --> RK["components/searcher/schema/_ranker.py"]
        S6 --> FF["components/searcher/final_filter.py"]
    end
```

## 搜索过滤器链

Search Pipeline 的 filters 参数经过多层转换：

```{mermaid}
sequenceDiagram
    participant C as Client
    participant MAP as mappers/search_filters.py
    participant P as Pipeline
    participant R as MemoryDbReader
    participant Q as QdrantEngine

    C->>MAP: SearchRequest.filters (DSL)
    MAP->>MAP: parse_search_dsl(filters)
    Note over MAP: 将用户 DSL 转为 FieldCondition[]
    
    MAP->>MAP: 添加默认 scope 条件
    Note over MAP: status=active (始终生效)
    Note over MAP: project_id scoping (多租户)
    
    P->>R: search_sparse(context, query, filters)
    R->>MAP: search_filter_to_qdrant(ctx, filters)
    Note over MAP: 将 SearchFilter → Qdrant Filter
    Note over MAP: 添加 account_id / project_id 硬过滤
    
    Q-->>Q: Qdrant Filter 执行
```

## Agentic Search 路径

MindMemOS 还支持一种 **LLM 驱动的多轮搜索**（agentic search）：

```{mermaid}
graph TB
    AS["AgenticSearchPipeline"] --> PL["Planner<br/>pipelines/search/agentic/planner.py"]
    PL -->|"LLM 规划<br/>生成 search plan"| TL["ToolRouter<br/>pipelines/search/agentic/tool_router.py"]
    
    TL -->|"bm25_search"| Q["MemoryDbReader.search_sparse()"]
    TL -->|"entity_search"| ER["EntityRecall<br/>components/searcher/entity_recall.py"]
    TL -->|"graph_search"| R["MemoryDbReader.list_memory_neighbor_scopes()"]
    
    TL --> LOOP["AgenticLoop<br/>pipelines/search/agentic/loop.py"]
    LOOP -->|"sufficiency check?"| SUF["SufficiencyChecker<br/>pipelines/search/agentic/sufficiency.py"]
    
    SUF -->|"足够"| WRAP["Wrapper → 返回结果"]
    SUF -->|"不足"| LOOP
```

**特点**：Agentic Search 不依赖单次向量检索，而是用 LLM 动态决定下一步搜索策略，适合需要多跳推理的查询。
