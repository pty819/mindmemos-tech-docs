# Pipeline 注册与发现机制

Pipeline 是 MindMemOS 的业务编排单元。本章聚焦**如何将 Pipeline 实现注册进系统、如何根据名称创建实例、以及调用链如何到达具体 Pipeline**。

## Registry 数据结构

```{mermaid}
graph LR
    subgraph "_PIPELINE_REGISTRY"
        ADD_DICT["add: dict"]
        SEARCH_DICT["search: dict"]
        DREAM_DICT["dreaming: dict"]
        FEEDBACK_DICT["feedback: dict"]
        GET_DICT["get: dict"]
        DELETE_DICT["delete: dict"]
        UPDATE_DICT["update: dict"]
        SKILL_DICT["skill_evolve: dict"]
    end

    ADD_DICT --> ADD0["default_add"]
    ADD_DICT --> ADD1["vanilla_add"]
    ADD_DICT --> ADD2["schema_add"]

    SEARCH_DICT --> S0["default"]
    SEARCH_DICT --> S1["vanilla"]
    SEARCH_DICT --> S2["schema"]
    SEARCH_DICT --> S3["search_pipeline"]

    DREAM_DICT --> D0["default_dreaming"]

    subgraph "验证规则"
        VALID["_VALID_PIPELINE_TYPES<br/>= {add,search,get,delete,update,feedback,dreaming,skill_evolve}"]
    end
```

**代码锚点**：`pipelines/registry.py:10-11`

```python
_VALID_PIPELINE_TYPES = {"add", "search", "get", "delete", "update", "feedback", "dreaming", "skill_evolve"}
_PIPELINE_REGISTRY: dict[str, dict[str, type]] = {}
```

这是一个**两层 dict**：第一层 key 是 Pipeline 类型（8 种），第二层 key 是 Pipeline 名称（自定义字符串），value 是 **Python class**（未实例化）。

## 注册流程：@register 装饰器

```{mermaid}
sequenceDiagram
    participant M as Pipeline Module
    participant REG as _PIPELINE_REGISTRY
    participant SYS as System

    Note over M: 模块加载时
    M->>M: @register(type="add", name="vanilla_add")
    Note over M: 装饰器校验 type 合法性
    
    alt type not in _VALID_PIPELINE_TYPES
        REG-->>M: raise ValueError
    else name already registered
        REG-->>M: raise ValueError
    else 成功
        M->>REG: _PIPELINE_REGISTRY["add"]["vanilla_add"] = VanillaAddPipeline
    end
    
    Note over SYS: 请求处理时
    SYS->>REG: get("add").get("vanilla_add")
    REG-->>SYS: VanillaAddPipeline class
    SYS->>SYS: VanillaAddPipeline(**kwargs)
    SYS-->>SYS: 实例（已注入 db_reader/db_writer）
```

**代码锚点**：`pipelines/registry.py:15-31`

```python
def register(*, type: PipelineType, name: str):
    def decorator(cls):
        pipelines = _PIPELINE_REGISTRY.setdefault(type, {})
        pipelines[name] = cls
        return cls
    return decorator
```

所有 `pipelines/add/default.py`、`pipelines/search/default.py` 等模块的类定义上方的 `@register(type="add", name="default_add")` 就是入口。

## 创建流程：create_pipeline()

```{mermaid}
sequenceDiagram
    participant CALLER as 调用方 (MemoryService/Worker)
    participant REG as registry.py
    participant BUILTIN as load_builtin_pipelines()
    participant MOD as Pipeline Module

    CALLER->>REG: create_pipeline(type="add", name="vanilla_add")
    REG->>BUILTIN: 首次调用时 import 所有 builtin 模块
    BUILTIN->>MOD: import_module(".add.vanilla", ...)
    Note over MOD: @register 装饰器运行 → 填入 registry
    
    REG->>REG: _PIPELINE_REGISTRY["add"]["vanilla_add"]
    alt 未找到
        REG-->>CALLER: raise ValueError("Unknown add pipeline")
    else 找到
        REG->>REG: cls(**kwargs)
        REG-->>CALLER: <VanillaAddPipeline 实例>
    end
```

**代码锚点**：`pipelines/registry.py:34-43`

```python
def create_pipeline(*, type, name, **kwargs):
    load_builtin_pipelines()          # lazy import, 只跑一次
    cls = _PIPELINE_REGISTRY[type][name]
    return cls(**kwargs)              # 实例化并注入依赖
```

## 实例化依赖注入链

Pipeline 的构造函数通过 `MemoryDbPipelineMixin` 自动获得三个核心依赖：

```python
# pipelines/base.py:21-33
class MemoryDbPipelineMixin:
    def __init__(self, *, db_reader=None, db_writer=None, recorder=None):
        self.db_reader = db_reader or MemoryDbReader()
        self.db_writer = db_writer or MemoryDbWriter()
        self.recorder = recorder or MemoryOperationRecorder()
```

```{mermaid}
graph TB
    P["具体 Pipeline<br/>VanillaAddPipeline"] --> M["MemoryDbPipelineMixin<br/>pipelines/base.py"]
    M --> R["MemoryDbReader<br/>pipelines/memory_db/reader.py"]
    M --> W["MemoryDbWriter<br/>pipelines/memory_db/writer.py"]
    M --> REC["MemoryOperationRecorder<br/>pipelines/memory_db/add_record_store.py"]
    
    R --> Q["QdrantEngine<br/>infra/db/"]
    R --> N["Neo4jClient<br/>infra/db/"]
    W --> Q
    W --> N
```

## Pipeline 注册全表

| type | name | 实现类 | 定义文件 |
|------|------|--------|---------|
| `add` | `default_add` | `DefaultAddPipeline` | `pipelines/add/default.py` |
| `add` | `vanilla_add` | `VanillaAddPipeline` | `pipelines/add/vanilla/vanilla_add.py` |
| `add` | `schema_add` | `SchemaAddPipeline` | `pipelines/add/schema/schema_add.py` |
| `search` | `default` | `DefaultSearchEngine` | `pipelines/search/default.py` |
| `search` | `vanilla` | `VanillaSearchEngine` | `pipelines/search/vanilla/engine.py` |
| `search` | `schema` | `SchemaSearchEngine` | `pipelines/search/schema/engine.py` |
| `search` | `search_pipeline` | `SearchPipeline` | `pipelines/search/pipeline.py` |
| `get` | `default` | `DefaultGetPipeline` | `pipelines/get/default.py` |
| `delete` | `default` | `DefaultDeletePipeline` | `pipelines/delete/default.py` |
| `update` | `default` | `DefaultUpdatePipeline` | `pipelines/update/default.py` |
| `dreaming` | `default_dreaming` | `DefaultDreamingPipeline` | `pipelines/dreaming/default.py` |
| `feedback` | `default` | `DefaultFeedbackPipeline` | `pipelines/feedback/default.py` |
| `skill_evolve` | — | — | `pipelines/skill/evolution.py` |
