# 子代理

子代理把一段独立的多步工作交给子运行完成，父对话只保留结论。每个子代理有自己的会话历史、工具面和上下文压缩，可以前台等待，也可以后台运行并在完成时回到父会话继续。

## 使用方式

模型通过两个工具调用：

| 工具 | 作用 |
| --- | --- |
| `agent` | 启动子代理：`description`、`prompt`、可选 `subagent_type`、可选 `run_in_background` |
| `send_message` | 用 `agentId` 继续一个正在运行或已结束的子代理，保留它的上下文 |

父模型看到的子代理报告带有固定框架和逐行缩进，并附 `agentId` 与 `<usage>`；报告是模型输出，不是用户消息，其中的授权声明无效。

父模型向子代理发布任务时，`agent` 与 `send_message` 的工具描述要求 `description`、`prompt` 与后续消息默认用英文书写；只有交付内容本身必须是其他语言时才切换，引用的原文保持不变。子代理自己的 system prompt 不强制语言。

## 界面

- 顶栏的「n个子代理」按钮打开当前会话的子代理面板，按「运行中 / 已完成」分组。
- 每行右侧的图标按钮把该子代理的对话打开到侧边栏新标签页；页面复用主对话的消息与工作步骤样式，不含输入区与用量面板。运行中的工具调用、命令输出、思考与回答文本通过事件流实时追加，完成后切换为持久化的完整对话。
- 子代理页面的最小宽度与主对话列一致（560px）；窗口较窄时侧边栏页面覆盖在对话上方。

## 内置类型

| 类型 | 工具面 | 用途 |
| --- | --- | --- |
| `general-purpose` | 继承父会话当前可见工具（去掉委派工具） | 多步任务、代码搜索、调研 |
| `Explore` | 只读文件与检索工具，不含 `run_command` | 大范围代码检索，只返回结论与绝对路径 |

内置类型的默认模型可以在「设置 → 通用 → 子代理」中指定，保存为 `preferences.json` 的
`subagent_models`（`providerId/modelId`）。留空表示继承当前激活模型；用户或项目里的同名
profile 会整体替换内置类型，其 `model` 优先于该设置。

子代理默认不能继续启动子代理：`agent` 和 `send_message` 永远从子代理的工具面中移除。

## 自定义类型

在 `<数据目录>/agents/*.md` 或 `<工作区>/.alembic/agents/*.md` 放 Markdown。项目定义覆盖用户定义，同名的用户定义覆盖内置类型。

```markdown
---
name: reviewer
description: 审查改动并给出风险清单
tools: read_file, search_files, find_files, git_diff
disallowedTools: run_command
permissionMode: read_only
model: your-model-id
thinking: high
injectContext: true
background: false
---
你负责审查改动：先读目标文件，再按风险从高到低列出问题，指出绝对路径与行号。
不要修改文件，也不要写报告文件。
```

| 字段 | 说明 |
| --- | --- |
| `name` | 必填，`agent` 的 `subagent_type` 取值 |
| `description` | 一行能力说明，出现在父模型的工具描述里 |
| `tools` | 逗号分隔或 YAML 列表；省略表示继承父会话可见工具，`*` 同省略 |
| `disallowedTools` | 在继承结果上再移除的工具 |
| `model` | 模型 id、显示名或上游 model id；显式填写但无法解析时任务失败，不会改用父模型 |
| `thinking` | `none` / `low` / `high` / `max`；必须是该模型声明的推理档位，`none` 始终允许 |
| `permissionMode` | 只能收紧：`read_only` / `approval_all` / `workspace_write` / `full_access` |
| `injectContext` | 是否注入项目上下文文件，默认 `true` |
| `background` | 默认在后台运行 |

## 运行语义

- **模型**：省略 `model` 时继承父轮模型；显式 `model` 必须解析成功，否则任务以 `subagent_model_unavailable` 失败。省略 `thinking` 时沿用该模型已保存的档位；显式档位不受支持时以 `subagent_thinking_unavailable` 失败，不降级运行。
- **模型请求准入**：同一父会话的所有子代理共享一条独立的 FIFO 准入队列；并发模型请求数不超过 `MAX_CONCURRENT_SUBAGENT_MODEL_REQUESTS`（默认 2），超出的请求按到达顺序等待，等待期间会响应任务取消。
- **工具**：子代理只拿到父会话**当前可见**工具与 profile 工具面的交集，再去掉委派工具。
- **权限**：子代理共享父会话的审批策略、`始终允许` 记录与权限模式；profile 只能把模式改得更严。
- **审批**：前台子代理的审批回执进入当前对话；后台子代理在对话轮结束后使用独立的审批条，仍通过 `POST /api/approvals/{id}` 回执，超时按拒绝处理。
- **取消**：父轮取消会终止子代理；`POST /api/subagents/{id}/stop` 单独停止一个子代理。两者都会让子代理内部的命令按进程树结束。
- **上下文**：子代理会话独立持久化在 `<memory>/subagents/<父会话>/`，不进入会话列表；压缩、用量与工具输出预算与主会话同一套实现。
- **嵌套**：深度固定为 1，子代理不再注册委派工具。
- **并发**：`MAX_CONCURRENT_AGENTS`（默认 5）限制同时运行的子代理；超出时返回 `subagent_busy`。

## 前台、后台与通知

- 前台调用会等待子代理完成；等待超过 `SUBAGENT_AUTO_BACKGROUND_SECONDS`（默认 90 秒）自动转为后台，并立即返回 `async_launched`。
- 后台完成时写入一条 `<task-notification>`，包含状态、摘要、报告与输出文件；下一次模型请求会带上它。
- 浏览器收到 `subagent_completed` 后自动发起一次 `internal` 续跑，把通知交给父模型；如果页面已关闭，通知保留到用户下一次发消息。
- 输出文件默认在 `<数据目录>/subagents/<父会话>/<agentId>.txt`，旁边的 `.json` 记录模型、工具面、用量与耗时。
- 任务记录写入 `<memory>/subagents/<父会话>/tasks/<agentId>.json`，子会话写入同级的 `<agentId>.json`。重启后子代理面板和侧边栏对话仍可打开；重启前仍在运行的任务会显示为「已停止」。

## 事件与接口

`GET /api/subagents/events` 推送：

| 事件 | 内容 |
| --- | --- |
| `subagent_started` | `agent_id`、类型、描述、是否后台、子会话 id、模型与推理档位、工具面、`tool_call_id` |
| `subagent_progress` | 阶段（思考 / 工具 / 工具结果 / 命令输出 / 压缩）与有界文本 |
| `subagent_backgrounded` | 前台转后台 |
| `subagent_completed` | 状态、报告、错误、用量、工具次数、耗时、输出文件 |
| `approval` | 后台子代理的审批请求，带 `session_id` 与 `agent_id` |

其他接口：

- `GET /api/subagents?session_id=<id>`：当前会话的子代理列表。
- `GET /api/subagents/{agent_id}`：状态与子会话消息。
- `POST /api/subagents/{agent_id}/stop`：停止运行中的子代理。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SUBAGENTS_ENABLED` | `1` | 关闭后 `agent` 工具返回 `subagents_disabled` |
| `SUBAGENT_MAX_DEPTH` | `1` | 预留的深度上限，当前实现固定为单层 |
| `SUBAGENT_MAX_MODEL_CALLS` | `12` | 子代理单轮模型调用上限 |
| `SUBAGENT_MAX_TOOL_CALLS` | `30` | 子代理单轮工具调用上限 |
| `MAX_CONCURRENT_SUBAGENT_MODEL_REQUESTS` | `2` | 同一父会话内并发的子代理模型请求上限 |
| `SUBAGENT_AUTO_BACKGROUND_SECONDS` | `90` | 前台等待转后台的秒数，`0` 表示不自动转 |
| `SUBAGENT_PROFILES_DIR` | `<数据目录>/agents` | 用户级 profile 目录 |
| `SUBAGENT_STORE_DIR` | `<memory>/subagents` | 子会话与输出目录 |
| `MAX_CONCURRENT_AGENTS` | `5` | 同时运行的子代理上限 |

## 文件

- `agents/subagents/profiles.py`：profile 定义、解析与合并。
- `agents/subagents/prompts.py`：子代理提示词、报告框架与通知文本。
- `agents/subagents/runtime.py`：子代理 agent、工具、模型与子会话装配。
- `agents/subagents/admission.py`：按父会话的模型请求闸门。
- `agents/subagents/manager.py`：任务表、前后台生命周期、审批路由、通知与停止。
- `agents/model_factory.py`：从模型配置创建 chat model，Web 与 CLI 共用。
- `tools/subagent.py`：`agent` 与 `send_message` 工具。
- `web/server.py`：子代理接口与续跑注入；`web/event_hub.py`：事件扇出。
