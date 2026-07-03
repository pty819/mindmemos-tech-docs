# Kafka 事件总线与 Worker 架构

异步路径的核心是 Kafka 事件总线。本章聚焦消息如何从 Pipeline 发出、如何被 Worker 消费、以及 Worker 如何复用 API 层的代码。

## 总体架构

```{mermaid}
graph TB
    subgraph "Producers (API Pipeline)"
        P_ADD["AddPipeline.add_async()"]
        P_DREAM["DreamingPipeline.dream()"]
        P_FB["FeedbackPipeline.feedback_async()"]
    end
    
    subgraph "Kafka Topics"
        T1["memory.add"]
        T2["memory.dreaming"]
        T3["memory.feedback"]
        T4["memory.episode"]
        T5["memory.add.drain"]
        T6["skill.evolve"]
    end
    
    subgraph "Consumer Workers (workers/)"
        W1["memory_add.py<br/>group: memory-add-worker"]
        W2["memory_dreaming.py<br/>group: memory-dreaming-worker"]
        W3["memory_feedback.py"]
        W4["schema_add_episode.py"]
        W5["schema_add_drain.py"]
        W6["skill_evolve.py"]
    end
    
    subgraph "Infrastructure"
        DISPATCHER["OrderedKeyedDispatcher<br/>infra/kafka/dispatcher.py"]
        REG_HANDLER["register_handler()<br/>infra/kafka/registry.py"]
    end

    P_ADD -->|"get_producer().send"| T1
    P_DREAM --> T2
    P_FB --> T3

    T1 --> DISPATCHER
    T2 --> DISPATCHER
    T3 --> DISPATCHER
    T4 --> DISPATCHER
    T5 --> DISPATCHER
    T6 --> DISPATCHER

    DISPATCHER -->|"调用"| W1
    DISPATCHER -->|"调用"| W2
    DISPATCHER -->|"调用"| W3
    DISPATCHER -->|"调用"| W4
    DISPATCHER -->|"调用"| W5
    DISPATCHER -->|"调用"| W6
    
    W1 -->|"复用"| ADD_P["AddPipeline.add_sync()<br/>同一行代码"]
    W2 -->|"复用"| DREAM_P["DreamingPipeline.dream_sync()"]
    W3 -->|"复用"| FB_P["FeedbackPipeline.feedback_sync()"]
```

## Worker 注册流程

```{mermaid}
sequenceDiagram
    participant APP as API app.py (lifespan)
    participant REG as workers/__init__.py
    participant KREG as infra/kafka/registry.py
    participant CONSUMER as Kafka Consumer
    participant DISP as OrderedKeyedDispatcher

    APP->>REG: register_workers()
    
    REG->>REG: from .memory_add import handle_memory_add
    REG->>REG: from .memory_dreaming import handle_memory_dreaming
    REG->>REG: from .skill_evolve import handle_skill_evolve
    
    REG->>KREG: register_handler("memory-add-worker", handle_memory_add)
    REG->>KREG: register_handler("memory-dreaming-worker", handle_memory_dreaming)
    Note over KREG: 注册 6 个 handler，以 group_id 为 key
    
    APP->>KREG: start_kafka()
    KREG->>CONSUMER: 为每个 group_id 创建 aiokafka Consumer
    Note over CONSUMER: consumer.subscribe([topic])
    
    CONSUMER->>DISP: submit(dispatch_key, message)
    Note over DISP: dispatch_key = project_id
    Note over DISP: 保证同一 project 内有序处理
    Note over DISP: global_max_concurrency 限制全局并发
```

**代码锚点**：`workers/__init__.py:11-32`

## OrderedKeyedDispatcher 内部机制

```{mermaid}
graph TB
    subgraph "OrderedKeyedDispatcher"
        SUB["submit(key, item)"] --> QUEUE["key→asyncio.Queue 映射"]
        QUEUE --> KW{"key 已有 worker?"}
        KW -->|"否"| SPAWN["asyncio.create_task(_run_key())"]
        KW -->|"是"| ENQUEUE["queue.put_nowait(item)"]
        
        SPAWN --> RUN["_run_key()"]
        RUN --> RLOOP["循环: queue.get_nowait()"]
        RLOOP --> SEM["global semaphore<br/>acquire"]
        SEM --> PROC["process(item)"]
        PROC --> COMP["on_complete(item)"]
        COMP --> RLOOP
    end
    
    subgraph "并发控制"
        GLOB["global_max_concurrency<br/>asyncio.Semaphore"]
        PERK["per_key_max_concurrency=1<br/>保证 key 内有序"]
        BUFF["max_buffered<br/>buffer 上限 → 背压"]
    end
```

**代码锚点**：`infra/kafka/dispatcher.py:25-143`

```python
class OrderedKeyedDispatcher:
    """Ordered keyed dispatcher owned by one Kafka consumer."""
    def __init__(self, *, global_max_concurrency, per_key_max_concurrency, max_buffered, process, on_complete):
        self._sem = asyncio.Semaphore(global_max_concurrency)
        self._per_key = per_key_max_concurrency
        ...
```

## Worker 处理链（以 memory_add 为例）

```{mermaid}
sequenceDiagram
    participant K as Kafka
    participant W as memory_add.py
    participant ALGO as api/algorithm.py
    participant P as AddPipeline
    participant DB as MemoryDbWriter

    K->>W: ConsumedMessage {context, input, add_record_id}
    
    W->>W: MemoryRequestContext.model_validate(body["context"])
    W->>W: AddPipelineInput.model_validate(body["input"])
    
    W->>ALGO: binding_for_memory_algorithm(context.memory_algorithm)
    ALGO-->>W: MemoryAlgorithmBinding(add_pipeline="vanilla_add")
    
    W->>W: create_pipeline(type="add", name="vanilla_add")
    Note over W: 和 API 路径用同一行代码
    
    W->>P: add_sync(payload, context, add_record_id)
    
    alt 成功
        P->>DB: apply_mutation_plan()
        DB-->>P: WriteResult
        W->>W: recorder.mark_add_completed()
    else 失败
        W->>W: recorder.mark_add_failed()
        W->>W: raise
    end
```

**关键设计**：Worker 不自己实现任何业务逻辑——它只是将 Kafka 消息反序列化后，调用和 API 同步路径**完全相同的 `add_sync()`** 方法。

## 6 个 Worker 一览

| Worker 文件 | Consumer Group | 消费 Topic | 核心调用 |
|------------|---------------|-----------|---------|
| `memory_add.py` | `memory-add-worker` | `memory.add` | `AddPipeline.add_sync()` |
| `memory_dreaming.py` | `memory-dreaming-worker` | `memory.dreaming` | `DreamingPipeline.dream_sync()` |
| `memory_feedback.py` | — | `memory.feedback` | `DefaultFeedbackPipeline.feedback_sync()` |
| `schema_add_drain.py` | — | `memory.add.drain` | Schema add buffer drain |
| `schema_add_episode.py` | — | `memory.episode` | Schema add episode 构建 |
| `skill_evolve.py` | — | `skill.evolve` | Skill 进化逻辑 |
