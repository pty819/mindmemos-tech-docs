# 知识模型设计理念

本章是 MindMemOS 的**知识白皮书**——不涉及具体代码和模块，只讨论系统对"知识"的理解：知识在系统中被定义成什么形态、为什么这样定义、以及这些定义如何支撑记忆的写入和检索。

## 知识的三种基本形态

MindMemOS 将一切知识归纳为三种基本形态，所有功能都围绕它们展开。

```{mermaid}
graph TB
    subgraph "外部输入（原始知识）"
        RAW["自然语言文本<br/>对话/文档/消息/文件"]
    end
    
    subgraph "系统内部（结构化知识）"
        M["Memory 记忆<br/>最小的知识单元"]
        E["Entity 实体<br/>知识中的关键对象"]
        R["Relationship 关系<br/>知识间的连接"]
    end
    
    subgraph "高阶产物"
        MEM["Memories 集合<br/>同 scope 的记忆组"]
        PROF["Profile<br/>用户画像"]
        EPI["Episodic<br/>情节性记忆"]
        SKILL["Skill<br/>可复用的技能模式"]
    end
    
    RAW -->|"提取"| M
    RAW -->|"识别"| E
    M -->|"通过关系连接"| R
    E -->|"被记忆提及"| R
    
    M --> MEM
    M --> PROF
    M --> EPI
    M --> SKILL
```

## Memory（记忆）——最小的知识单元

### 什么是 Memory

Memory 是 MindMemOS 中**最小的、自包含的知识单元**。一条 Memory 代表一个原子性的事实或观察。

```python
# 概念模型（非真实代码）
Memory {
    id:          str           # 全局唯一
    content:     str           # 知识正文（自然语言）
    type:        MemoryType    # 知识分类
    status:      "active" | "archived"
    created_at:  datetime
    
    # 身份上下文（10 字段）
    project_id:  str           # 租户隔离
    user_id:     str | None    # 用户归属
    session_id:  str | None    # 会话归属
    agent_id:    str | None    # Agent 归属
    ...
    
    # 语义向量
    bm25_vector: SparseVector  # 稀疏检索向量
    
    # 溯源
    root_id:     [str]         # 版本链根
}
```

**设计原则**：一条 Memory 是自描述的——它携带了足够多的身份字段（account/project/user/session/agent…）以实现多租户硬隔离，同时保留溯源能力（root_id 链）。

### 为什么选择自然语言作为 Memory 的载体？

这不是偶然的——系统的所有输入输出都是自然语言，所以最自然的存储粒度也是自然语言。相比"结构化三元组"（subject-predicate-object）的方案：

| 维度 | 自然语言 Memory | 结构化三元组 |
|------|----------------|-------------|
| **信息密度** | 一段话承载多个事实 | 一条三元组承载一个事实 |
| **提取代价** | 一次 LLM 调用即可提取 | 需要多次 LLM 调用分解 |
| **人类可读** | 直接可读 | 需要重新组装 |
| **检索召回** | 全文/向量检索自然匹配 | 需要图遍历或 SPARQL |
| **精度** | 模糊但有上下文 | 精确但丢失上下文 |

**取舍**：MindMemOS 选择了**以自然语言为主体、实体和关系作为辅助索引**的混合方案。Memory 本身不是纯文本块——它附带 BM25 稀疏向量和实体提及关系，支持高精度检索。但检索结果的主体始终是**自然语言段落**，不是三元组。

## Entity（实体）——知识的锚点

### 什么是 Entity

Entity 是知识中**可命名、可区分、可关联的关键对象**。它从 Memory 的文本中被提取出来，充当跨 Memory 连接的锚点。

```python
Entity {
    id:             str          # UUID5(project_id, type, name) → 确定性的
    name:           str          # 实体名称
    canonical_name: str | None   # 规范名（用于合并别名）
    entity_type:    str | None   # 实体类型（person/org/location/...）
    description:    str | None   # 描述
    aliases:        [str]        # 别名列表
    confidence:     float        # 提取置信度
    search_fields:  [str]        # 关联的 Memory 文本（用于实体搜索）
}
```

**关键设计**：Entity ID 是**确定性的**——由 `UUID5(project_id, entity_type, canonical_name)` 生成，相同的实体在同一项目中永远指向同一个 UUID。这使得实体合并不需要加锁或协调。

### Entity 的两种角色

```
作为检索入口                  作为图节点
    │                            │
    ▼                            ▼
用户搜索"压力异常"             Memory─MENTIONS→Entity←MENTIONS─Memory
    │                            │
    ▼                            ▼
→ 命中 Entity pressure_anomaly  → 关联到所有提及该实体的 Memory
→ 返回关联的 Memory             → 实现多跳推理
```

## Relationship（关系）——知识之间的连接

MindMemOS 定义了 8 种图关系类型，分成三类：

| 类别 | 关系 | 连接 | 用途 |
|------|------|------|------|
| **提及** | `MENTIONS` | Memory → Entity | 标记一段记忆提到了哪个实体 |
| **关联** | `RELATES_TO` | Memory → Memory | 强关联（因果/时序/主题） |
| **关联** | `RELATED_TO` | Memory → Memory | 弱关联（语义相关） |
| **属性** | `HAS_PROPERTY_MEMORY` | Entity → Memory | 实体的属性值记忆 |
| **时序** | `NEXT_IN_PROPERTY_TIMELINE` | Memory → Memory | 同一属性的时间线 |
| **溯源** | `EXTRACTED_FROM` | Memory → Source | 记忆从哪个源文件提取 |
| **溯源** | `MENTIONED_IN_SOURCE` | Entity → Source | 实体在哪个源文件中被提及 |
| **谱系** | `DERIVED_FROM` | Memory → Memory | 版本层级（consolidate 后的派生链） |

```{mermaid}
graph TB
    subgraph "实体层"
        E1["Entity<br/>压力传感器"]
        E2["Entity<br/>密封圈"]
    end
    
    subgraph "记忆层"
        M1["Memory<br/>压力异常 detected"]
        M2["Memory<br/>检查密封圈正常"]
        M3["Memory<br/>干法清洗后恢复"]
    end
    
    subgraph "源层"
        S1["Source<br/>维修报告 2026-06"]
        S2["Source<br/>传感器日志"]
    end
    
    M1 -->|"MENTIONS"| E1
    M1 -->|"MENTIONS"| E2
    M2 -->|"MENTIONS"| E2
    M3 -->|"MENTIONS"| E1
    M1 -->|"RELATES_TO"| M2
    M2 -->|"RELATES_TO"| M3
    M1 -->|"EXTRACTED_FROM"| S1
    E1 -->|"MENTIONED_IN_SOURCE"| S2
```

**设计要点**：关系类型是有向的、带类型的边。Neo4j 中的边标签就是上表的 8 种。关注"连接什么和不连接什么"比关注"如何遍历"更重要。

## 7 种 Memory 类型

MindMemOS 定义了 7 种 Memory Type，对应不同来源的知识：

```{mermaid}
graph LR
    subgraph "原始输入"
        RAW["对话/文档/消息"]
    end
    
    subgraph "按来源分类"
        RAW -->|"直接陈述"| FACT["fact<br/>事实性知识"]
        RAW -->|"长期观察"| PROFILE["profile<br/>画像/偏好"]
        RAW -->|"事件经历"| EXP["experience<br/>经历经验"]
        RAW -->|"情节回放"| EPI["episodic<br/>情节性记忆"]
        RAW -->|"工具调用"| TOOL["tool_trace<br/>工具调用痕迹"]
        RAW -->|"技能模式"| SKILL["skill_candidate<br/>可复用技能"]
        RAW -->|"文件知识"| FILE["file_knowledge<br/>文档知识"]
    end
    
    subgraph "检索行为"
        FACT -->|"BM25 全文"| SEARCH["通用检索"]
        PROFILE -->|"实体关联"| SEARCH
        EXP -->|"时序排序"| SEARCH
        TOOL -->|"结构化匹配"| SEARCH
        FILE -->|"BM25 检索"| SEARCH
    end
```

## 知识图谱的拓扑

整个系统的知识图谱呈**星形-链式混合拓扑**：

```{mermaid}
graph TB
    subgraph "星形（Entity 为中心）"
        E["Entity"] --- M1["Memory A"]
        E --- M2["Memory B"]
        E --- M3["Memory C"]
    end
    
    subgraph "链式（Memory 间）"
        M4["Memory D"] -->|"RELATES_TO"| M5["Memory E"]
        M5 -->|"RELATES_TO"| M6["Memory F"]
    end
    
    subgraph "属性时间线"
        E2["Entity"] -->|"HAS_PROPERTY"| M7["属性值 v1"]
        M7 -->|"NEXT_IN"| M8["属性值 v2"]
        M8 -->|"NEXT_IN"| M9["属性值 v3"]
    end
```

这份拓扑直接决定了系统的检索策略（见第 15 章）。

## 为什么这样设计？

MindMemOS 的知识模型围绕三个设计目标展开：

1. **检索优先**— 系统的主要使用场景是"我记得提到过 X，找出来"。自然语言 Memory + BM25 向量索引为此优化。
2. **可推理**— Memory 之间通过 Entity 和关系连接，支持图遍历（多跳推理）。这与纯向量检索系统的关键区别。
3. **可溯源**— 每条 Memory 都携带完整的身份上下文。谁说的、什么时候、在哪个会话里、从哪个源提取——全部保留。

```{mermaid}
graph LR
    subgraph "设计三角"
        A["检索优先<br/>BM25 + Entity 入口"]
        B["可推理<br/>图遍历 + 多跳"]
        C["可溯源<br/>10 字段身份上下文"]
    end
    
    A --- B
    B --- C
    C --- A
```

**不是三元组存储**，不是文档库，不是纯向量库——是**以自然语言为主体、实体为索引、关系为推理通路的混合知识模型**。
