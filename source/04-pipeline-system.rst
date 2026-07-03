Pipeline 体系总览
=================

MindMemOS 的 Pipeline 架构是系统的核心编排层。每个 Pipeline 实现一个 **Protocol**，
通过 ``pipelines/registry.py`` 的 ``@register`` 装饰器注册，按名称路由。

Pipeline 类型
-------------

.. code-block:: text

   pipelines/
   ├── add/          # 记忆写入（同步 + 异步）
   ├── search/       # 记忆检索（多引擎 + Agentic）
   ├── dreaming/     # 离线记忆巩固
   ├── feedback/     # 在线反馈修正
   ├── delete/       # 记忆删除
   ├── update/       # 记忆修改
   ├── get/          # 记忆列举
   ├── skill/        # Skill 生命周期管理
   ├── memory_db/    # 数据库读/写/记录基础设施
   ├── base.py       # 共享依赖（MemoryDbPipelineMixin）
   ├── registry.py   # Pipeline 注册表
   └── utils/        # DTO 工厂和时间工具

Pipeline 协议
-------------

每个 Pipeline 类型对应一个 Protocol，位于 ``pipelines/<type>/base.py``：

.. code-block:: python

   class AddPipeline(Protocol):
       async def add_sync(self, inp: AddPipelineInput, context: MemoryRequestContext) -> AddPipelineSyncResult
       async def add_async(self, inp: AddPipelineInput, context: MemoryRequestContext) -> AddPipelineAsyncResult
       async def has_pending(self, context: MemoryRequestContext) -> bool

   class SearchPipeline(Protocol):
       async def search(self, inp: SearchPipelineInput, context: MemoryRequestContext) -> SearchPipelineResult

   class SearchEngine(Protocol):
       name: str
       async def search_candidates(self, inp, context, *, options=None) -> list[MemorySearchItem]

   class DreamingPipeline(Protocol):
       async def dream(self, inp, context) -> DreamingPipelineResult

依赖注入方式
------------

所有 Pipeline 继承 ``MemoryDbPipelineMixin``：

.. code-block:: python

   class MemoryDbPipelineMixin:
       def __init__(self, *, db_reader=None, db_writer=None, recorder=None):
           self.db_reader = db_reader or MemoryDbReader()
           self.db_writer = db_writer or MemoryDbWriter()
           self.recorder = recorder or MemoryOperationRecorder()

子组件通过构造函数注入，允许测试时 mock：

.. code-block:: python

   pipeline = VanillaAddPipeline(
       text_preprocessor=mock_preprocessor,
       memory_extractor=mock_extractor,
       candidate_deduplicator=mock_dedup,
       ...
   )

Pipeline 注册表
---------------

``pipelines/registry.py`` 维护了一个名称 → 实现的映射：

.. code-block:: python

   @register(type="add", name="vanilla_add")
   class VanillaAddPipeline(MemoryDbPipelineMixin):
       ...

   @register(type="search", name="vanilla_search_engine")
   class VanillaSearchEngine(MemoryDbPipelineMixin):
       ...

搜索时通过 ``get_pipeline(type, name)`` 查找：
``search_pipeline`` 配置项指定使用哪个引擎。

数据流模式
----------

所有 Pipeline 遵循统一的数据流模式：

.. code-block:: text

   API Request
       │
       ▼
   Service Layer (api/services/)
       │ 解析请求 → 创建 Input DTO + RequestContext
       ▼
   Pipeline (编排层)
       │ Phase 1 → Phase 2 → ... → Phase N
       │ 每个 Phase 调用一个 Component
       ▼
   Memory DB (Reader / Writer)
       │ Qdrant + Neo4j
       ▼
   Result DTO → API Response
