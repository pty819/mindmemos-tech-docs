# Feedback Pipeline 模块调用链

Feedback Pipeline 是**学习闭环**。它接收用户显式或隐式的反馈信号，转化为记忆的强化/弱化/更新。

## 反馈类型分岔

```{mermaid}
graph TB
    MS["MemoryService.feedback()"] --> FB["DefaultFeedbackPipeline"]
    
    FB --> TYPE{"feedback.type?"}
    
    TYPE -->|"explicit"| EXP["explicit路径"]
    TYPE -->|"implicit"| IMP["implicit路径"]
```

## Explicit Feedback 路径

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant FB as DefaultFeedbackPipeline
    participant EXP as ExplicitPlanner
    participant W as MemoryDbWriter

    SVC->>FB: feedback_sync(payload, context)
    FB->>FB: payload = to_feedback_pipeline_input(request)
    
    FB->>EXP: plan(feedback)
    Note over EXP: 解析用户提供的:
    Note over EXP: - score (1-5)
    Note over EXP: - relevance
    Note over EXP: - comments
    
    EXP-->>FB: UpdateCommand[]
    
    loop 每个 update command
        FB->>W: update_memory(ctx, command)
        Note over W: 更新 memory 的:
        Note over W: - reinforcement_count += delta
        Note over W: - metadata.feedback_score = score
        W-->>FB: MutationResult
    end
    
    FB-->>SVC: FeedbackPipelineResult
```

## Implicit Feedback 路径

```{mermaid}
sequenceDiagram
    participant SVC as MemoryService
    participant FB as DefaultFeedbackPipeline
    participant SIG as SignalDetector
    participant LLM as LLMClient
    participant Q as QueryRewriter
    participant ACT as ActionPlanner
    participant W as MemoryDbWriter

    SVC->>FB: feedback_sync(payload, context)
    
    FB->>SIG: detect(input)
    Note over SIG: 检测隐式信号:
    Note over SIG: - click / re-read
    Note over SIG: - dwell time
    Note over SIG: - skip / discard
    Note over SIG: - search 后未点击
    
    SIG-->>FB: SignalResult(type, strength)
    
    alt strength > threshold
        FB->>LLM: chat(SEARCH_DECISION_PROMPT, signal, context)
        Note over LLM: 判断是否需要对 query 重写/扩展
        LLM-->>FB: SearchDecision(need_rewrite, reason)
        
        alt need_rewrite
            FB->>LLM: chat(QUERY_REWRITE_PROMPT, original_query, signal)
            LLM-->>FB: rewritten_query
            
            FB->>ACT: plan(signal, rewritten_query)
            Note over ACT: 决定 memory 操作:
            Note over ACT: - reinforce: 增加 reinforcement_count
            Note over ACT: - demote: 降低分数
            Note over ACT: - patch: 更新 metadata
            
            ACT-->>FB: ActionPlan[]
            
            loop 每个 action
                FB->>W: apply_mutation_plan(ctx, plan)
                W-->>FB: MutationResult
            end
        end
    end
    
    FB-->>SVC: FeedbackPipelineResult
```

## 模块依赖关系

```{mermaid}
graph TB
    subgraph "Feedback Pipeline"
        FB["DefaultFeedbackPipeline<br/>pipelines/feedback/default.py"]
        FB_EXP["ExplicitPlanner<br/>pipelines/feedback/explicit_planner.py"]
        FB_IMP["ImplicitPlanner<br/>pipelines/feedback/implicit.py"]
        FB_SIG["SignalDetector<br/>components/feedback/signal.py"]
        FB_QR["QueryRewriter<br/>components/feedback/query_rewriter.py"]
        FB_ACT["ActionPlanner<br/>components/feedback/action_planner.py"]
        FB_ROUNDS["RoundsManager<br/>components/feedback/rounds.py"]
    end
    
    FB --> FB_EXP
    FB --> FB_IMP
    FB_IMP --> FB_SIG
    FB_IMP --> FB_QR
    FB_IMP --> FB_ACT
    FB_ACT --> FB_ROUNDS
    
    subgraph "下游"
        FB --> W["MemoryDbWriter"]
        FB --> LLM["LLMClient"]
    end
```

## Feedback 的异步路径

同 Add Pipeline 一样，Feedback 也支持 `mode="async"`：

```{mermaid}
graph LR
    FB["DefaultFeedbackPipeline"] -->|"mode=async"| K["get_producer().send('memory.feedback', ...)"]
    K --> W["memory_feedback.py worker"]
    W --> FB2["DefaultFeedbackPipeline.feedback_sync()"]
    
    FB -->|"mode=sync"| SYNC["feedback_sync() 直写"]
```

**代码锚点**：`pipelines/feedback/default.py` — `feedback_sync()` 和 `feedback_async()` 的定义。
