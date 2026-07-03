# 知识拆解入库流程设计

本章回答一个问题：**一段原始文本进入系统后，经历了哪些设计阶段，最终变成可检索的结构化知识？**

## 总览：从原始文本到知识

```{mermaid}
graph TB
    subgraph "输入"
        INPUT["原始文本<br/>对话/文档/消息/文件"]
    end
    
    subgraph "第一阶段：预处理"
        P1["语言检测<br/>zh/en/mixed"]
        P2["分词 & BM25 分析<br/>→ tokens"]
        P3["实体识别<br/>→ named entities"]
        P4["标准化<br/>→ normalized text"]
        P5["去重<br/>→ content hash"]
    end
    
    subgraph "第二阶段：结构化"
        S1["构建 Memory<br/>→ 知识单元"]
        S2["构建 Entity<br/>→ 知识锚点"]
        S3["构建 Vector<br/>→ BM25 稀疏向量"]
        S4["构建 MENTIONS 关系<br/>→ Memory ↔ Entity"]
    end
    
    subgraph "第三阶段：持久化"
        D1["Qdrant 写入<br/>记忆向量 + 实体"]
        D2["Neo4j 写入<br/>图关系"]
    end
    
    subgraph "第四阶段：后处理（异步）"
        A1["Dreaming 巩固<br/>→ 关系检测 & 行动"]
        A2["Feedback 强化<br/>→ 学习信号"]
    end
    
    INPUT --> P1
    P1 --> P2
    P2 --> P3
    P2 --> P4
    P3 --> P5
    P4 --> P5
    
    P5 --> S1
    P5 --> S2
    S2 --> S4
    S1 --> S3
    S1 --> S4
    
    S1 --> D1
    S2 --> D1
    S3 --> D1
    S4 --> D2
    
    D1 -.->|"定时/触发"| A1
    D1 -.->|"用户信号"| A2
```

## 第一阶段：预处理——从文本到可处理的语言单元

### 为什么需要预处理层？

原始文本不是干净的输入。它可能混合多种语言、包含噪声字符、实体表述不一致。预处理层的目标是**将原始文本归一化为系统可以一致处理的结构**。

```{mermaid}
sequenceDiagram
    participant RAW as 原始文本
    participant P as TextPreprocessor
    participant L as _language
    participant LX as _lexical
    participant ENT as _entity
    participant NORM as _normalize
    participant HASH as _hashing

    RAW->>P: preprocess_text(text)
    
    P->>L: detect_language(text)
    L-->>P: LanguageResult(zh/en/mixed, confidence)
    
    P->>LX: analyze(text, lang)
    LX-->>P: BM25TokenizationResult(terms, bm25_text)
    
    P->>ENT: extract_entities(text, lang)
    ENT-->>P: Entity[](name, type, offsets, confidence)
    
    P->>NORM: normalize(text)
    NORM-->>P: normalized_text
    
    P->>HASH: content_hash(normalized_text)
    HASH-->>P: hex_digest
    
    P-->>RAW: PreprocessedResult(tokens, entities, normalized_text, content_hash, lang)
```

**设计要点**：
- 语言检测发生在最前面——中文和英文的分词策略完全不同
- 实体识别在标准化**之前**——因为标准化可能改变实体在原文中的偏移量
- content_hash 用于去重——同一段文本不会写入两次

### 为什么不需要 Deep LLM 参与预处理？

预处理阶段完全由**轻量规则和统计方法**完成：
- 语言检测：CJK 字符比例判定
- 分词：基于词典和规则（中文）或 whitespace+stemming（英文）
- 实体识别：正则 + 字典匹配
- 标准化：Unicode NFC 归一化、空白压缩

**设计决策**：预处理必须**零 LLM 调用**。如果每写一条 Memory 都调一次 LLM 做实体提取，延迟和成本都不可接受。LLM 只参与**可选的深度提取**（VanillaAddPipeline 的 6 阶段全量提取）。

## 第二阶段：结构化——从预处理产物到知识单元

```{mermaid}
graph TB
    subgraph "预处理产物"
        TOK["tokens"]
        ENT["entities"]
        NORM["normalized_text"]
        HASH["content_hash"]
    end
    
    subgraph "构建 Memory"
        M1["memory_id = uuid4()"]
        M2["content = normalized_text"]
        M3["type = infer_type(input)"]
        M4["身份字段 = request context"]
        M5["root_id = [memory_id]"]
    end
    
    subgraph "构建 Entity"
        E1["entity_id = UUID5(project_id, type, name)"]
        E2["去重: 相同 (project_id, type, name) → 同一实体"]
        E3["search_fields = 关联的 memory 文本"]
    end
    
    subgraph "构建 Vector"
        V1["SparseVectorEncoder.encode_document(tokens)"]
        V2["BM25 indices + values"]
    end
    
    subgraph "构建 Relationship"
        R1["MENTIONS: Memory → Entity"]
    end
    
    TOK --> V1
    NORM --> M2
    ENT --> E1
    ENT --> R1
    E2 --> E3
```

**关键设计细节**：

### Memory ID 为什么用 uuid4？

Entity ID 用确定性 UUID5，Memory ID 用随机 uuid4。原因是：**Entity 需要去重合并**（同名实体在不同输入中应该合并），而 **Memory 不需要去重**（同一段话可能在不同时间写入，应该各自保留）。

### 身份注入

每条 Memory 在构建时被注入 10 个身份字段，全部来自请求的 `MemoryRequestContext`：

```python
memory = MemoryWrite(
    memory_id=..., 
    account_id=context.account_id,
    project_id=context.project_id,
    api_key_uuid=context.api_key_uuid,
    user_id=context.user_id,
    app_id=context.app_id,
    session_id=context.session_id,
    agent_id=context.agent_id,
    request_id=context.request_id,
    ...
)
```

**这 10 个字段是知识溯源的基础**。下游检索时可以根据任意维度的身份做过滤——"只查这个用户的"、"只查这个会话的"、"只查这个 Agent 产生的"。

### Entity 去重逻辑

```
输入 A: "压力传感器 MFC-1 异常"
输入 B: "MFC-1 输出流量偏差 5%"
           ↓
Entity 1: {name="MFC-1", type="sensor", canonical_name="MFC-1"}
           ↓
两条 Memory 都 MENTIONS 这个 Entity → 图中间接连接
```

## 第三阶段：持久化——知识落地

```{mermaid}
graph LR
    subgraph "结构化产物"
        M["MemoryWrite"]
        E["EntityWrite"]
        V["VectorWrite (BM25)"]
        R["GraphRelationship"]
    end
    
    subgraph "Qdrant（向量层）"
        QM["collection: memories<br/>payload: content, type, identity<br/>vector: BM25 sparse"]
        QE["collection: entities<br/>payload: name, type, search_fields"]
    end
    
    subgraph "Neo4j（图层）"
        NM["node: Memory"]
        NE["node: Entity"]
        NR["edge: MENTIONS"]
    end
    
    M --> QM
    V --> QM
    E --> QE
    M --> NM
    E --> NE
    R --> NR
```

**双写的设计含义**：
- **Qdrant** 负责检索（谁提到了 X？）——通过 BM25 稀疏向量
- **Neo4j** 负责推理（哪些 Memory 共享同一个 Entity？）——通过图遍历
- 两条路径互补，不是冗余

## 第四阶段：后处理——知识的生命延续

知识入库后并非终点。Dreaming Pipeline 会在后台定期扫描记忆，检测可改进的关系并执行合并/归档；Feedback Pipeline 会根据用户信号强化或弱化记忆。详见后续章节。

## Add Pipeline 在知识流中的角色

| 实现 | 特点 | 知识流 |
|------|------|--------|
| `default_add` | 轻量，零 LLM 调用 | 预处理 → 结构化 → 双写 |
| `vanilla_add` | 6 阶段全量提取，**LLM 密集型** | 同上 + LLM 深度实体/关系/冲突检测 |
| `schema_add` | Schema-aware 提取 | 同上 + Schema 学习 & 属性提取 |

三种实现共享相同的知识模型和持久化层，区别只在结构化的精度和 LLM 调用量之间权衡。
