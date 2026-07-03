Feedback Pipeline — 在线反馈修正
================================

Feedback Pipeline 是 MindMemOS 的**在线学习闭环**，支持显式反馈和隐式反馈两种模式。

显式反馈
--------

由 ``api/routes.py`` 的 ``POST /v1/memory/feedback`` 端点触发。

.. code-block:: text

   用户反馈文本 → FeedbackPipelineInput
       │ 包含 feedback + messages + recalled_memories
       ▼
   ExplicitFeedbackPlanner
       │ LLM 分析反馈内容、对话上下文和召回的记忆
       ▼
   FeedbackActionResult (四种动作)
       ├─ add    — 新增记忆（补充缺失信息）
       ├─ update — 更新已有记忆（修正错误）
       ├─ delete — 删除记忆（清除错误信息）
       └─ noop   — 无需修改

隐式反馈
--------

自动分析对话记录，无需用户主动反馈。由以下子组件实现：

.. code-block:: text

   QueryRewriter — 从操作记录中提取查询语句
       │
       ▼
   RoundsCollector — 收集紧凑对话轮次（query + response 对）
       │
       ▼
   SignalDetector — 检测每轮中的负反馈信号
       │ 按分类：
       │  - task_temporary    — 当前任务临时修正
       │  - scenario_specific — 场景特定偏好
       │  - long_term         — 应永久保存的偏好
       ▼
   ImplicitFeedbackPlanner — 对反馈信号生成修正动作
       │
       ▼
   FeedbackExecutor — 执行动作（调用 add/update/delete 操作）

隐式反馈的信号源包括：

- 用户重复相同请求（记忆未命中）
- 用户修正助手输出（"不是 X，是 Y"）
- 用户明确表示不满
- 会话中的语义转折

Action Planner
--------------

``components/feedback/action_planner.py`` 负责生成具体的反馈修正动作：

.. code-block:: python

   class FeedbackAddAction(BaseModel):
       action: Literal["add"] = "add"
       result_memory_id: str | None = None
       after_content: str           # 新记忆内容

   class FeedbackUpdateAction(BaseModel):
       action: Literal["update"] = "update"
       target_memory_id: str
       before_content: str
       after_content: str

   class FeedbackDeleteAction(BaseModel):
       action: Literal["delete"] = "delete"
       target_memory_id: str
       before_content: str

整个反馈回路通过 Kafka 异步解耦，``workers/memory_feedback.py`` 消费并执行。

Feedback Pipeline 实现了 MindMemOS 的**自我修正机制**，每次用户的"不是这样的"，
都在悄悄地优化记忆质量。
