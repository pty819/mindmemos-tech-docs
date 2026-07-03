# 知识反馈闭环设计

知识的价值是动态的——不是写入时设定的。一条 Memory 可能因为被多次引用而变得重要，也可能因为被后续信息推翻而变得过时。**Feedback Pipeline** 是 MindMemOS 让知识**随时间改进**的机制。

## 反馈闭环总览

```{mermaid}
graph TB
    subgraph "知识生命周期"
        WRITE["写入 Memory"] --> RETRIEVE["检索 Memory"]
        RETRIEVE --> USE["用户/Agent 使用"]
        USE --> FEEDBACK["产生反馈信号"]
        FEEDBACK --> UPDATE["更新 Memory 属性"]
        UPDATE --> RETRIEVE
    end
    
    subgraph "反馈信号来源"
        EXP["Explicit 显式评分<br/>用户主动给的分"]
        IMP["Implicit 隐式信号<br/>点击/跳过/停留时间"]
    end
    
    subgraph "Memory 属性变更"
        R1["reinforcement_count += 1"]
        R2["feedback_score = new_score"]
        R3["metadata.patch()"]
    end
    
    EXP --> FEEDBACK
    IMP --> FEEDBACK
    FEEDBACK --> R1
    FEEDBACK --> R2
    FEEDBACK --> R3
```

## 显式反馈（Explicit）

用户（或上游 Agent）明确告知系统一条记忆的价值：

```python
# 概念性的 API 调用
POST /v1/memory/feedback
{
  "type": "explicit",
  "memory_ids": ["mem_xxx", "mem_yyy"],
  "score": 5,          # 1-5
  "relevance": 0.95,   # 0-1
  "comment": "这个诊断记录非常准确"
}
```

显式反馈的处理链很简单：

```{mermaid}
sequenceDiagram
    participant U as 用户/Agent
    participant SVC as MemoryService
    participant FB as FeedbackPipeline
    participant W as MemoryDbWriter

    U->>SVC: feedback(type=explicit, memory_ids, score)
    SVC->>FB: feedback_sync()
    FB->>FB: explicit_planner.plan()
    Note over FB: 解析 score → update delta
    FB->>W: update_memory(reinforcement_count +delta, feedback_score=score)
    W-->>FB: OK
    FB-->>SVC: FeedbackPipelineResult
```

## 隐式反馈（Implicit）

多数反馈不是用户主动给的，而是从行为中推导的：

```{mermaid}
graph TB
    subgraph "隐式信号检测"
        S1["搜索后点击了第 3 条结果<br/>而非第 1 条"]
        S2["同一 session 内<br/>反复搜索同一实体"]
        S3["搜索后没有点击任何结果<br/>→ 检索质量差"]
        S4["用户在对话中<br/>引用/重述了某条记忆"]
    end
    
    subgraph "信号处理"
        S1 -->|"强度: 中"| D["SignalDetector"]
        S2 -->|"强度: 高"| D
        S3 -->|"强度: 低"| D
        S4 -->|"强度: 很高"| D
    end
    
    subgraph "行动决策"
        D -->|"strength > threshold"| LLM["LLM evaluate"]
        LLM -->|"need_rewrite?"| QR["Query Rewriter"]
        QR -->|"rewritten query"| PLAN["Action Planner"]
        PLAN -->|"reinforce/patch/demote"| W["MemoryDbWriter"]
    end
```

隐式反馈的处理路径比显式复杂——它需要 LLM 判断信号的含义：

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant FB as DefaultFeedbackPipeline
    participant SIG as SignalDetector
    participant LLM as LLMClient
    participant W as MemoryDbWriter

    SVC->>FB: feedback_sync(type=implicit, query, clicked_memory)
    
    FB->>SIG: detect(query, behavior)
    SIG-->>FB: Signal(type="click_skip", strength=0.7)
    
    alt strength > threshold
        FB->>LLM: chat(SEARCH_DECISION_PROMPT)
        Note over LLM: 这个信号说明什么？
        Note over LLM: 用户没点击 top-1 而是点了 top-3
        Note over LLM: 是否说明 top-1 不相关，top-3 更相关？
        LLM-->>FB: {need_rewrite: true, reason: "ranking mismatch"}
        
        FB->>LLM: chat(QUERY_REWRITE_PROMPT)
        LLM-->>FB: rewritten_query
        
        FB->>FB: action_planner.plan()
        Note over FB: demote top-1, reinforce top-3
        FB->>W: apply_mutation_plan()
    end
```

**设计取舍**：隐式反馈走 LLM 路径体现了"宁可少改，不可错改"的原则。如果系统不确定信号含义，就什么都不做。虚假的正反馈（错误强化）比遗漏的负反馈更有害。

## 强化计数（Reinforcement Count）的设计

每条 Memory 有一个 `reinforcement_count` 字段。它不是简单的计数器，而是有语义的：

| 事件 | count 变化 | 含义 |
|------|-----------|------|
| Memory 被检索命中且用户点击 | `+= 1` | 确认有效 |
| 同一用户重复引用 | `+= 2` | 高价值 |
| 用户明确打高分 | `+= 5` | 强确认 |
| 用户明确打低分 | `-= 3` | 质量差 |
| Dreaming 检测到被覆盖 | `-= 1` | 过时 |

强化计数影响**后续检索排序**——在 Reranker 中作为排序信号之一：

```
final_score = bm25_score * 0.6 + recency * 0.2 + reinforcement_normalized * 0.2
```

## 反馈-检索闭环

feedback 不仅影响目标 Memory，还通过检索记录的关联影响**未来的检索**：

```{mermaid}
graph LR
    A["用户搜索 '压力异常'"] --> B["搜索结果: Memory A, B, C"]
    B --> C["用户点击了 B，没有点 A"]
    C --> D["Feedback: demote A, reinforce B"]
    D --> E["下一次搜索 '压力异常'"]
    E --> F["Memory B 排名上升<br/>Memory A 排名下降"]
```

这个闭环是自动运转的——不需要用户意识到系统在学习。

## 反馈的数据流

所有 feedback 操作和 search 操作都会被记录到 audit store，形成完整的数据输入：

```
search_record_v1:
  - request_id
  - query
  - recalled_memories[]  ← 本次检索返回了哪些 Memory
  - scores
  - clicked_memory_id?   ← 用户点了哪个（后续通过 feedback 补充）
  - created_at

add_record_v1:
  - request_id
  - input (messages)
  - status (queued/processing/completed/failed)
  - memories[]  ← 这次 add 产生了哪些 Memory
  - skill_bindings
  - created_at, completed_at
```

这些记录被 `RecentActivityCollector` 读取，作为 Dreaming Pipeline 的 Scope 来源。

## 设计原则总结

| 原则 | 理由 |
|------|------|
| **显式路径快，隐式路径慢** | 显式信号可信度高，直接更新；隐式信号需 LLM 裁决 |
| **宁可漏改，不可错改** | 虚假强化比遗漏强化更有害 |
| **检索即学习** | 每次检索被记录，feedback 可以随时关联 |
| **强化计数是多信号之一** | 不单靠计数排序，与 BM25 score 和时效性联合使用 |
| **Feedback 记录可审计** | 所有 feedback 操作都被持久化，供后续分析 |
