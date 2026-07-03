# Dreaming Pipeline 模块调用链

Dreaming 是**离线记忆巩固**——不是实时请求处理，而是后台定期跑的任务。它把活跃的记忆 scope 拿出来，用 LLM 分两轮做关系检测和行动编排。

## 处理流程总图

```{mermaid}
graph TB
    subgraph "触发入口"
        API["API POST /dreaming"] --> DREAM
        KAFKA["Kafka Worker<br/>memory_dreaming.py"] --> DREAM
    end
    
    DREAM["DefaultDreamingPipeline<br/>pipelines/dreaming/default.py"] --> SCOPE["_collect_scopes()"]
    
    subgraph "Scope 收集阶段"
        SCOPE --> ACT["RecentActivityCollector<br/>components/activity/collector.py"]
        ACT -->|"ActivityScope[]"| MERGE["_merge_scopes()"]
        
        SCOPE --> GRAPH["MemoryDbReader<br/>list_memory_neighbor_scopes()"]
        GRAPH -->|"GraphNeighborScope[]"| MERGE
        
        MERGE -->|"ConsolidationScope[]<br/>按 score 排序"| LOOP["逐 scope 处理"]
    end
    
    subgraph "每 scope 两轮 LLM"
        LOOP --> R1["第一轮: Relation Detection"]
        R1 --> LLM1["LLMClient.chat()<br/>RELATION_DETECTION_PROMPT"]
        LLM1 -->|"DetectedMemoryIssueGroup"| R2{"有 issues?"}
        
        R2 -->|"是"| R2B["第二轮: Action Planning"]
        R2B --> LLM2["LLMClient.chat()<br/>ACTION_PLANNING_PROMPT"]
        LLM2 -->|"ConsolidationAction[]"| APPLY["_apply_action()"]
        
        R2 -->|"否"| NEXT["跳到下一 scope"]
    end
    
    subgraph "Action 执行"
        APPLY -->|"consolidate"| W1["MemoryDbWriter<br/>写入新 MemoryWrite"]
        APPLY -->|"archive"| W2["MemoryDbWriter<br/>归档旧 Memories"]
        APPLY -->|"relate"| W3["MemoryDbWriter<br/>写 RELATES_TO / UPDATE"]
        APPLY -->|"split"| W4["MemoryDbWriter<br/>拆分记忆"]
    end
```

## Scope 收集阶段调用链

Dreaming Pipeline 第一步是找到需要处理的记忆 scope。

```{mermaid}
sequenceDiagram
    participant D as DefaultDreamingPipeline
    participant ACT as RecentActivityCollector
    participant R as MemoryDbReader
    
    D->>D: _collect_scopes()
    
    D->>ACT: collect_recent_activity(project_id)
    Note over ACT: 从近期 add/feedback 事件中<br/>提取热点 entity / property
    ACT-->>D: ActivityScope[]
    
    D->>R: list_memory_neighbor_scopes(memory_ids)
    
    Note over R: Neo4j Cypher 查询:
    Note over R: (1) shared_entity: Memory→MENTIONS→Entity←MENTIONS←Memory
    Note over R: (2) direct_relation: Memory→RELATES_TO→Memory
    Note over R: (3) attach_direct_neighbors_to_entity_scopes
    
    R-->>D: GraphNeighborScope[]
    
    D->>D: _merge_scopes(activity_scopes, graph_scopes)
    Note over D: 合并 → ConsolidationScope[]
    Note over D: 按 score 排序，取 top N
```

**代码锚点**：`pipelines/dreaming/default.py:77-100`（`DefaultDreamingPipeline.__init__`）和 `_collect_scopes` 方法。

## 图遍历：list_memory_neighbor_scopes 内部

```{mermaid}
graph TB
    R["MemoryDbReader.list_memory_neighbor_scopes()"] --> S{"sources"}
    S -->|"shared_entity"| SE["list_memories_by_shared_entities()"]
    S -->|"direct_memory_relation"| DR["list_direct_related_memories()"]
    
    SE -->|"Cypher"| CYPHER1["MATCH (seed:Memory)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(mentioned:Memory)"]
    CYPHER1 --> RES1["GraphNeighborScope[]<br/>(entity_id, entity_name, memory_ids)"]
    
    DR -->|"Cypher"| CYPHER2["MATCH (seed:Memory)-[r:RELATES_TO|RELATED_TO]-(related:Memory)"]
    CYPHER2 --> RES2["DirectRelatedMemory[]"]
    
    RES1 --> MERGE["合并 scopes"]
    RES2 --> MERGE
    
    MERGE --> ATTACH["attach_direct_neighbors<br/>到 entity scopes"]
```

## 两轮 LLM 调用链

```{mermaid}
sequenceDiagram
    participant D as DefaultDreamingPipeline
    participant R as MemoryDbReader
    participant LLM as LLMClient
    participant W as MemoryDbWriter

    Note over D: 第一轮
    D->>R: get_memories(scope.seed_memory_ids)
    R-->>D: MemoryView[]
    
    D->>LLM: chat(RELATION_DETECTION_PROMPT, memories)
    Note over LLM: 检测: modes, relations, anomalies, gaps
    Note over LLM: 输入: scope 内所有 memory 文本
    Note over LLM: 输出: 结构化 DetectedMemoryIssueGroup
    LLM-->>D: DetectedIssueGroup(relation_changes=[], gaps=[], ...)
    
    alt 有需要处理的 issues
        Note over D: 第二轮
        D->>LLM: chat(ACTION_PLANNING_PROMPT, issues)
        Note over LLM: 输入: Detection 阶段发现的 issues
        Note over LLM: 输出: ConsolidationAction[]
        Note over LLM: 支持: consolidate/archive/relate/split/stash
        LLM-->>D: ConsolidationAction[]
        
        loop 每个 action
            D->>D: _apply_action(action)
            
            alt action.type == "consolidate"
                D->>W: apply_mutation_plan(with new MemoryWrite)
            else action.type == "archive"
                D->>W: apply_mutation_plan(with delete commands)
            else action.type == "relate"
                D->>W: apply_mutation_plan(with RELATES_TO relationships)
            else action.type == "update"
                D->>W: apply_mutation_plan(with memory updates)
            end
        end
    end
```

**关键设计**：每 scope 消耗 **2 次 LLM 调用**（Detection + Action Planning）。如果 scope 内无 issues，跳过第二轮。
