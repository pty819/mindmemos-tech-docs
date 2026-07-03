集成方式
========

MindMemOS 提供四种集成方式，覆盖从原生 SDK 到插件化集成。

Python SDK
----------

已发布 PyPI：``mindmemos-sdk``。通过 ``MindMemOSClient`` 操作记忆：

.. code-block:: python

   from mindmemos_sdk import MindMemOSClient
   from mindmemos_sdk.memory import DialogueMessage

   # 方式1：复用本地 auth 配置
   with MindMemOSClient() as client:
       result = client.memory.add(
           messages=[DialogueMessage(role="user", content="我喜欢喝冰美式")]
       )
       for item in result.memories:
           print(item.operation, item.memory_id, item.content)

       memories = client.memory.search("咖啡偏好", top_k=5)
       for hit in memories:
           print(hit.id, hit.memory)

   # 方式2：显式传参
   client = MindMemOSClient(
       base_url="https://mindmemos.cn",
       api_key="mk_xxx",
       user_id="u_123",
   )

CLI
---

随 SDK 一起安装。CLI 子命令覆盖所有操作：

.. code-block:: bash

   # 认证
   uv run mindmemos auth

   # 写入
   mindmemos memory add --content "我喜欢喝冰美式" --role user

   # 检索
   mindmemos memory search "咖啡偏好" --top-k 5

   # 更新/删除
   mindmemos memory update --memory-id <id> --content "我现在更喜欢拿铁"
   mindmemos memory delete --memory-id <id>

   # 反馈
   mindmemos memory feedback --text "刚才召回的偏好不准确"

   # 巩固
   mindmemos memory dreaming --sync

   # 诊断
   mindmemos doctor

CLI 拥有完整的 ``--json`` 输出模式，方便脚本调用。

HTTP API
--------

通过仓库内 skill 目录下的 ``SKILL.md`` 定义 Agent 集成方式：

- **Hermes Agent**：安装 ``mindmemos-cli`` skill
- **OpenClaw**：npm 安装 ``@mindmemos/openclaw-plugin``
- **Codex** / **Claude Code**：通过 CLI 接口对接

SKILL.md 配置示例
^^^^^^^^^^^^^^^^^

位于 ``skills/mindmemos-cli/SKILL.md``：

.. code-block:: markdown

   ---
   name: mindmemos-cli
   description: Give an AI agent persistent, cross-session long-term memory
   ---

   # MindMemOS CLI

   mindmemos <group> <command> [args] [options]
   mindmemos auth
   mindmemos memory search <query>
   mindmemos memory add --content <text>
   ...

CLI 的 ``SKILL.md`` 同时定义了何时使用何种操作：

.. list-table:: CLI 操作对照表
   :header-rows: 1

   * - 意图
     - 操作
     - 说明
   * - 记住新事实
     - add
     - 服务端自动去重/合并
   * - 查询已有记忆
     - search
     - fast（低延迟）或 agentic
   * - 查看全部
     - get
     - 无查询，按 filter 列举
   * - 修正错误/过时
     - update
     - 按 memory_id 精确修正
   * - 反馈质量
     - feedback
     - 显式或隐式分析
   * - 离线巩固
     - dreaming
     - 定期执行，非每轮调用
