附录 B：补充技术细节
=====================

以下内容来自对源码的深度遍历，补充正文未展开的技术细节。

B.1 错误体系设计
================

MindMemOS 有一个分层的异常体系：

.. code-block:: text

   errors/
   ├── base.py       — MemoryError（所有异常的基类 + error_code 枚举）
   ├── api.py        — ApiError（含 HTTP status_code）
   ├── config.py     — ConfigError, ConfigNotInitializedError
   ├── llm.py        — LLM 相关错误
   ├── memory.py     — MemoryNotFoundError, MemoryConflictError
   ├── skill.py      — SkillVersionError, SkillNotFoundError
   └── activity.py   — 活动记录错误

.. code-block:: python

   class MemoryError(Exception):
       """所有 MindMemOS 异常的基类。"""
       error_code: str = "internal_error"

   class ApiError(MemoryError):
       """API 层异常，内置 HTTP 状态码。"""
       status_code: int = 500
       code: str = "internal_error"
       message: str = ""

       def to_response(self) -> JSONResponse:
           return JSONResponse(
               status_code=self.status_code,
               content={"code": self.code, "message": self.message, "data": None},
           )

.. admonition:: 设计意图

   1. **统一响应格式**：所有 API 错误返回 ``{"code", "message", "data"}`` 结构。
   2. **分层捕获**：API 层捕获 ApiError → 返回 HTTP 响应；Pipeline 层捕获
      MemoryError → 记录日志；组件层只抛业务异常，不关心 HTTP。
   3. **错误码枚举**：``error_code`` 字符串（如 ``"memory_not_found"``）可在
      SDK 侧做精确的错误分支处理。

B.2 认证系统设计
================

``api/auth/registry.py`` 中的 ``AuthRegistry`` 支持四种认证方式并可链式组合：

.. code-block:: python

   class AuthHandler(Protocol):
       async def authenticate(self, request: Request) -> AuthContext | None: ...

   class AuthRegistry:
       def __init__(self):
           self._handlers: list[AuthHandler] = []

       def register(self, handler: AuthHandler) -> None:
           self._handlers.append(handler)  # 有序：APIKey → GatewayJWT → Internal

       async def authenticate(self, request: Request) -> AuthContext:
           for handler in self._handlers:
               result = await handler.authenticate(request)
               if result is not None:
                   return result
           raise ApiError(status_code=401, code="unauthorized")

四种认证模式的适用场景：

.. list-table:: 认证模式
   :header-rows: 1

   * - 模式
     - 凭证位置
     - 适用场景
   * - APIKeyAuth
     - ``Authorization: Bearer mk_xxx``
     - 外部 SDK/CLI 调用
   * - GatewayJWTAuth
     - ``Authorization: Bearer <gateway_jwt>``
     - 网关代理后的请求
   * - InternalTokenAuth
     - ``X-Internal-Token`` 请求头
     - 服务间内部调用
   * - ChainedAuth
     - 组合多种方式
     - 需兼容旧认证方案的过渡期

``AuthContext`` 携带完整的身份信息并在请求周期内通过 ``Request.state`` 传递：

.. code-block:: python

   @dataclass
   class AuthContext:
       account_id: str
       project_id: str
       api_key_uuid: str
       scopes: list[str]
       user_id: str | None = None
       identity_provider: str | None = None

B.3 Worker 幂等性设计
=====================

Kafka 的 at-least-once 语义意味着同一条消息可能被多次消费。MindMemOS 的 worker
通过两种机制保证幂等性：

**机制 1：dedup_metadata_key**

.. code-block:: python

   class MemoryDbMemoryUpdateCommand(BaseModel):
       dedup_metadata_key: str | None = None
       """如果已存在相同 key 的 metadata 值，跳过本次更新。"""

在 ``MemoryDbWriter.update_memory()`` 中，执行前检查：

.. code-block:: text

   1. 从 Qdrant 读取当前 memory payload
   2. 如果 payload.metadata[dedup_metadata_key] == 本次的 metadata_patch 值
   3. → 跳过更新（幂等）
   4. 否则 → 执行更新

**机制 2：add_record 状态机**

每个 add_record 有 ``consolidation_status`` 字段。Worker 在执行前读取该状态：

.. code-block:: text

   if record.consolidation_status == "done":
       return  # 已完成，跳过
   # 否则执行并标记 done

**机制 3：DLQ（Dead Letter Queue）**

当 worker 处理某条消息失败超过最大重试次数后：

.. code-block:: text

   1. 将原始消息（含 context + input）+ 错误信息写入 DLQ topic
   2. 提交 offset，继续消费后续消息（防止阻塞）
   3. 运维工具可以从 DLQ 重放

B.4 Reranker 降级链
===================

``llm/rerank.py`` 中的 RerankClient 设计了多级降级：

.. code-block:: python

   class RerankClient:
       async def rerank(self, query, texts, top_k):
           try:
               return await self._model_rerank(query, texts, top_k)   # L1: 交叉编码器
           except Exception:
               logger.warning("model rerank failed, falling back to keyword overlap")
               return await self._keyword_rerank(query, texts, top_k) # L2: 关键词重叠

           async def _keyword_rerank(self, query, texts, top_k):
               # 降级方案：计算 query 和每个 text 的 token 交集
               scores = [len(set(query_tokens) & set(text_tokens)) / len(query_tokens) for text in texts]
               return sorted_indices_by_score(scores, top_k)

这种设计保证了 reranker 的高可用：当付费的 cohere rerank 模型不可用时，
系统自动降级到纯关键词重叠排序，不会因为 reranker 故障导致搜索完全不可用。

B.5 数据集市与审计
==================

``add_record_v1`` 和 ``search_record_v1`` 是两个专门的审计集合，记录所有
写入和检索操作。它们的设计意图：

.. list-table:: 审计集合
   :header-rows: 1

   * - 集合
     - 用途
     - 生命周期
   * - add_record_v1
     - 记录每次记忆写入（含完整输入消息、提取出的记忆列表、处理状态）
     - 受 TTL 管理（Dreaming 基于此选择 hot scopes）
   * - search_record_v1
     - 记录每次检索（含 query、filters、召回结果列表）
     - 用于改进检索质量和隐式反馈分析

这些记录**不参与检索**——它们没有向量索引，仅通过 payload 字段过滤。

.. code-block:: python

   class AddRecordPoint(BaseModel):
       point_id: str
       input_messages: list[dict]       # 完整输入消息
       memory_payloads: list[dict]      # 提取出的记忆列表
       consolidation_status: str        # pending / done
       consolidation_run_id: str | None # Dreaming 运行 ID
       # + 完整的身份字段（project_id, user_id, session_id...）

审计记录使 Dreaming Pipeline 能够在**不重新计算**的情况下选择 hot scopes：
只需根据 ``add_record_v1`` 的时间窗口和状态字段判断哪些记忆需要巩固。
