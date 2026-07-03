# 知识巩固（Dreaming）设计

知识的价值不只在写入的那一刻。一段记忆在存储后，可能和其他记忆产生新的关联、可能被后续信息修正、可能因为时间推移而变得不重要。**Dreaming Pipeline** 是 MindMemOS 的离线知识巩固机制，类似于人类的睡眠巩固——在后台定期回顾和重组已有知识。

## 为什么需要离现巩固？

系统在在线写入时做出的决策是**局部最优**的：

```
在线写入时：
  Memory 1: "压力异常"     ← 不知道后面会有 Memory 2
  Memory 2: "密封圈老化"   ← 不知道前面有 Memory 1
  两者通过 Entity 间接关联，但没有直接关系边
  
离现巩固后：
  Memory 1 → "caused_by" → Memory 2 ← 新检测到的因果链
```

Dreaming Pipeline 解决了**三个在线写入无法解决的问题**：

```{mermaid}
graph TB
    subgraph "在线写入的限制"
        L1["局部视角 — 每条 Memory 独立写入<br/>，不知道整体图谱结构"]
        L2["无回溯能力 — 无法修正<br/>之前写得不准确的记忆"]
        L3["无跨 Memory 推理 — 无法<br/>检测多条记忆之间的隐含关系"]
    end
    
    subgraph "Dreaming 解决"
        S1["全局视角 — 扫描 scope 内<br/>所有 Memory，发现整体模式"]
        S2["回溯修正 — 合并重复、<br/>归档过时、拆分过粗"]
        S3["关系检测 — 用 LLM<br/>跨 Memory 发现因果关系"]
    end
```

## Dreaming 的两阶段设计

每个 scope 的巩固分两个 LLM 阶段，中间有明确的输出契约：

```{mermaid}
sequenceDiagram
    participant D as Dreaming Pipeline
    participant R as MemoryDbReader
    participant LLM as LLM Client
    participant W as MemoryDbWriter

    Note over D: ===== 阶段 0: Scope 收集 =====
    
    D->>D: _collect_scopes()
    D->>R: list_memory_neighbor_scopes()
    R-->>D: GraphNeighborScope[]
    
    D->>D: 按 score 排序，取 top N
    
    loop 每个 scope
        Note over D: ===== 阶段 1: 关系检测 =====
        
        D->>R: get_memories(scope.memory_ids)
        R-->>D: MemoryView[] (scope 内所有记忆)
        
        D->>LLM: chat(RELATION_DETECTION_PROMPT, memories)
        Note over D: 输入: scope 内所有 Memory 文本
        Note over D: 任务: 检测 modes/relations/anomalies/gaps
        Note over D: 输出: 结构化 DetectedMemoryIssueGroup
        LLM-->>D: DetectedIssueGroup
        
        alt 无需要处理的 issues
            Note over D: 跳过，节约一次 LLM 调用
        else 有 issues
            Note over D: ===== 阶段 2: 行动规划 =====
            
            D->>LLM: chat(ACTION_PLANNING_PROMPT, issues)
            Note over D: 输入: Detection 阶段发现的 issues
            Note over D: 任务: 生成具体的 consolidate/split/archive 行动
            Note over D: 输出: ConsolidationAction[]
            LLM-->>D: ConsolidationAction[]
            
            loop 每个 action
                D->>D: _apply_action(action)
                D->>W: apply_mutation_plan()
            end
        end
    end
```

## 阶段 0：Scope 收集——巩固什么？

Dreaming 不是扫全库——它只巩固**活跃的 memory scope**。Scope 来自两个源头：

```{mermaid}
graph TB
    subgraph "Scope 来源"
        ACT["RecentActivityCollector<br/>近期活跃的实体/属性"]
        GRAPH["MemoryDbReader.list_memory_neighbor_scopes<br/>图邻居 scope"]
    end
    
    subgraph "合并"
        ACT --> MERGE["_merge_scopes()"]
        GRAPH --> MERGE
        MERGE --> SORTED["按 score 排序 → ConsolidationScope[]"]
    end
    
    subgraph "score 影响因素"
        S1["新近度: 最近 24 小时内的记忆"]
        S2["活跃度: 被 search 多次命中"]
        S3["密集度: scope 内 Memory 数量"]
        S4["反馈信号: 用户显式标记过"]
    end
    
    S1 --> MERGE
    S2 --> MERGE
    S3 --> MERGE
    S4 --> MERGE
```

## 阶段 1：关系检测——发现什么？

LLM 在这个阶段对 scope 内的所有 Memory 做**跨文本分析**。检测的输出是一个结构化报告：

```
DetectedMemoryIssueGroup {
  relation_changes: [
    // 建议新增或修改的 Memory 间关系
    {type: "relate", source: "Memory A", target: "Memory B", relation: "CAUSED_BY"}
  ]
  split_suggestions: [
    // 建议将一条过粗的 Memory 拆成多条
    {memory_id: "...", reason: "包含两个独立事实"}
  ]
  merge_suggestions: [
    // 建议合并多条重复或互补的 Memory
    {memory_ids: [...], reason: "描述同一事件的不同来源"}
  ]
  archive_suggestions: [
    // 建议归档的过时 Memory
    {memory_id: "...", reason: "已被后续信息覆盖"}
  ]
  anomalies: [
    // 可疑或不一致的记忆
    {description: "Memory A 说 X，Memory B 说 ¬X"}
  ]
}
```

**设计要点**：LLM 不直接修改数据库，它只输出**结构化的行动建议**。所有修改由 Pipeline 在下一阶段执行。

## 阶段 2：行动规划——执行什么？

基于 Detection 的输出，LLM 生成一个或多个具体的 ConsolidationAction：

| Action 类型 | 行为 | 数据库操作 |
|-----------|------|-----------|
| `consolidate` | 将多条 Memory 合成为一条新的摘要 Memory，在旧的和新的之间建立 `DERIVED_FROM` 边 | `apply_mutation_plan(new MemoryWrite + DERIVED_FROM)` |
| `split` | 将一条 Memory 拆成多条更细粒度的 | `apply_mutation_plan(new MemoryWrites + archive old)` |
| `archive` | 将一条 Memory 标记为 `archived` | `apply_mutation_plan(status=archived)` |
| `relate` | 在两条 Memory 之间建立 `RELATES_TO` 关系 | `apply_mutation_plan(new RELATES_TO edge)` |
| `update` | 修改 Memory 的 metadata | `apply_mutation_plan(memory update)` |

```{mermaid}
graph LR
    subgraph "巩固前"
        BEFORE["Memory A: '压力异常，怀疑密封圈'<br/>Memory B: '密封圈检查正常已排除'<br/>独立写入，无关联"]
    end
    
    subgraph "巩固后"
        AFTER["Memory C: '压力异常排查记录'<br/>   ├─ DERIVED_FROM → Memory A<br/>   └─ DERIVED_FROM → Memory B<br/>Memory A: archived<br/>Memory B: archived<br/>Memory A ─RELATES_TO→ Memory B"]
    end
    
    BEFORE -->|"consolidate + archive"| AFTER
```

## LLM 调用成本控制

Dreaming 的 LLM 调用成本受两个因素控制：

```{mermaid}
graph TB
    subgraph "成本控制"
        C1["scope 数量上限<br/>max_scopes_per_run"]
        C2["每个 scope<br/>最多 2 次 LLM 调用"]
        C3["`如果 Detection 无 issues<br/>→ 跳过 Action Planning`"]
    end
    
    subgraph "触发频率"
        T1["API POST /dreaming<br/>手动触发"]
        T2["Kafka Worker<br/>定时/事件驱动"]
    end
    
    T2 -->|"每 N 分钟不超过一次"| D["DefaultDreamingPipeline"]
    D --> C1
    D --> C2
    D --> C3
```

## 巩固后的知识图谱变化

Dreaming 运行前后，知识图谱的拓扑会发生变化：

```{mermaid}
graph TB
    subgraph "巩固前"
        M1["Memory A: 压力异常"] -->|"MENTIONS"| E1["Entity: 密封圈"]
        M2["Memory B: 密封圈已换"] -->|"MENTIONS"| E1
    end
    
    subgraph "巩固后"
        M1A["Memory A (archived)"] -->|"MENTIONS"| E1
        M2A["Memory B (archived)"] -->|"MENTIONS"| E1
        M3["Memory C: consolidated"] -->|"MENTIONS"| E1
        M1A -->|"DERIVED_FROM"| M3
        M2A -->|"DERIVED_FROM"| M3
        M1A -->|"RELATES_TO"| M2A
    end
```

**设计要点**：
- 旧 Memory 不会被物理删除——只标记为 `archived`，仍然可回溯
- `DERIVED_FROM` 边保留了完整的版本链
- Entity 保持不变——无论 Memory 如何变化，实体始终是稳定的锚点

## 设计总结

| 设计决策 | 理由 |
|---------|------|
| 两阶段 LLM（Detection → Action） | 分离"发现"和"执行"，降低单次 LLM 调用的复杂度 |
| Scope 优先 | 不是全库扫描，而是聚焦活跃区域 |
| 旧记忆保留为 archived | 支持完整的历史回溯和版本追踪 |
| DERIVED_FROM 链 | 保留知识演变的谱系 |
| 可跳过 | 如果 scope 没问题，不浪费 LLM 调用 |
