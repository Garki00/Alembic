# ALEMBIC

ALEMBIC 是一个本地优先的智能体工作区。后端使用 Python + FastAPI，前端使用 Vue 3 + Vite + TypeScript，两者通过 `/api` 与 SSE 通信。模型配置、工具、审批、工作区和会话都保存在本机，浏览器打开界面即可使用。

- 默认使用模型原生工具调用，也可以切换到文本 ReAct 循环。
- 读写工作区文件、搜索、打补丁、执行命令、管理后台进程与 Git。
- 读取文档和图片，抓取网页、搜索网页、下载文件。
- 多轮会话持久化、上下文压缩、任务清单、向用户提问。
- 按权限模式审批工具调用；支持只读模式、命令策略和凭据路径保护。
- 多供应商、多模型，每个模型可以单独设置推理等级。
- 命令输出实时推送；后台任务有独立视图，完成时主动通知并续跑。
- 输入区支持导入附件、手动压缩上下文，并实时显示上下文占用比例。

架构图、数据流和不可破坏的约定见 [HANDOVER.md](HANDOVER.md)。

## 目录

- [1. 快速开始](#1-快速开始)
- [2. 首次使用与界面](#2-首次使用与界面)
- [3. 模型与供应商](#3-模型与供应商)
- [4. Agent 执行与上下文](#4-agent-执行与上下文)
- [5. 工具](#5-工具)
- [6. 审批与安全边界](#6-审批与安全边界)
- [7. 命令行](#7-命令行)
- [8. 数据、配置与接口](#8-数据配置与接口)
- [9. 开发、测试与打包](#9-开发测试与打包)

## 1. 快速开始

环境要求：

- Python 3.11 及以上
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm
- 命令沙盒：Windows 10 1809+ x64，本地 NTFS 工作区；首次安装需要管理员权限

首次安装依赖：

```powershell
uv sync

cd frontend
npm install
cd ..
```

命令执行需要一次性初始化原生沙盒（用当前账户打开管理员 PowerShell；不要换成另一个管理员账户）：

```powershell
.\scripts\setup-sandbox.ps1
```

之后日常启动不需要管理员权限。没有初始化时，文件工具和对话仍可使用，但所有命令工具会返回 `sandbox_unavailable`。

一键启动后端与前端：

```powershell
.\scripts\dev.ps1
```

脚本会检查虚拟环境、启动 FastAPI 和 Vite，并在浏览器打开 `http://127.0.0.1:5173`。首次运行时如果缺少 `frontend/node_modules`，脚本会自动安装前端依赖。常用参数：

```powershell
.\scripts\dev.ps1 -NoBrowser                  # 不自动打开浏览器
.\scripts\dev.ps1 -BackendPort 8001 -FrontendPort 5174
.\scripts\dev.ps1 -SkipInstall                # 已装好依赖时跳过检查
```

如果 npm 安装失败，可以指定镜像源：

```powershell
$env:ALEMBIC_NPM_REGISTRY = "https://registry.npmmirror.com"
.\scripts\dev.ps1
```

也可以分别启动：

```powershell
# 终端 1：后端 http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m web

# 终端 2：前端 http://127.0.0.1:5173
cd frontend
npm run dev
```

后端只提供 `/api`，根地址 `http://127.0.0.1:8000` 返回 API 说明，不展示页面。浏览器请打开前端地址。

构建前端产物：

```powershell
cd frontend
npm run build       # vue-tsc 类型检查 + vite build
npm run preview     # 4173 端口预览，/api 仍代理到后端
```

命令行的用法见[第 7 节](#7-命令行)。

## 2. 首次使用与界面

### 2.1 初始化向导

数据目录里还没有可用供应商时，首次打开会显示初始化向导：

1. **供应商与模型**：以图标卡片列出深度求索（DeepSeek）、OpenAI、Anthropic Claude、阿里云千问、硅基流动和自定义供应商。选择模板会自动填入 Base URL；在同一个供应商下可以添加一个或多个模型。也可以点「从上游获取」读取 `{base_url}/models`：标准接口通常只返回模型 ID，OpenRouter 等会额外提供上下文长度、最大输出、图片/推理能力，前端会一并填入；没有能力字段时使用本地模型家族默认值。
2. **工作步骤与终端**：选择工作步骤展示档位、收起时机，以及命令工具默认使用的终端。这些选项与「设置 → 通用」一致。

保存后供应商会出现在「设置 → 供应商」中。初始化完成不会自动创建工作区：对话区会显示「选择一个工作区」的提示，选择或添加目录后才出现输入框。在没有任何对话的工作区里直接输入并发送，会自动创建一条新对话。

如果 `.env` 或当前终端里已经同时设置了 `DEEPSEEK_API_KEY` 与 `MODEL_NAME`，首次启动会自动生成一个供应商并跳过向导。需要重新查看向导时，见[重置配置](#83-重置配置)。

### 2.2 工作区与对话

界面采用纸白与薄荷绿的编辑排版风格。左侧栏按工作区分组：

- 顶部搜索框同时匹配工作区名称、路径和对话标题。
- 「＋」添加工作区，走目录浏览，每个工作区可以单独起名。
- 每个工作区行有「＋ 新建对话」和「⋯ 菜单」（重命名 / 删除）。删除工作区会连同它的对话一起删除，操作前会确认。
- 对话按工作区分组，历史保存在 `memory/`，重新打开应用后仍可恢复。

对话区支持流式输出、Markdown 渲染、上下文占用提示和停止生成。停止生成会中断当前轮的前台命令，并停止当前会话正在运行的后台任务；其他会话的后台任务不受影响。工具调用与模型思考会按实际顺序显示为工作步骤；命令执行期间会实时显示输出。一轮结束且存在文件改动时，独立卡片汇总修改了多少个文件、总新增 / 删除行数，点击文件名可以在右侧栏以 Diff View 打开，悬停文件名可以看到改动预览。

### 2.3 模型切换与推理等级

输入框右下角显示当前模型。点开后：

- 先显示供应商图标标签，选中供应商后列出它下面的模型。
- 点模型即可切换；底部「管理供应商…」进入设置。
- 模型切换按钮和发送按钮之间有「推理」按钮。点开后显示可拖动、自动吸附的横向滑条，滑条下方标注各档位，拖动即可切换当前等级。

### 2.4 审批卡与提问卡

需要确认或补充信息时，输入栏会替换成审批卡或提问卡：

- 审批卡先给一句人话说明，再按需展示分步说明、风险原因、原始命令或改动内容。
- 提问卡提供选项按钮和自由输入框，选项一行一个。
- 选择结果保留在对话输出里，不会把待处理按钮混在正文中。

### 2.5 右侧栏

右上角按钮展开 / 收起右侧栏。展开后左右侧边栏都可以拖动边缘调整宽度，宽度保存在本机。

- 标签页可以打开工作区文件、Diff 和内置浏览器。手动新建的标签最多 3 个；从对话打开的文件或链接不占这个配额，标签总数上限为 10。
- 工作区文件树可以预览文本与 Markdown：代码 / 文本左侧显示行号，Markdown 默认预览并可切换「原文」。
- 从变更总结打开时进入 Diff View，显示增删行；整文件写入缺少旧内容时只显示新增内容。
- 二进制、图片、压缩包等格式会明确提示不支持预览。
- 内置浏览器初始不访问任何页面，输入网址后才加载。

### 2.6 设置

设置入口是左上角齿轮，模型菜单底部的「管理供应商…」也会打开供应商页。设置分为三页：

- **通用**：权限模式（只读 / 全部审批 / 工作区内修改 / 完全权限，默认工作区内修改；切到完全权限需要二次确认）、终端偏好（PowerShell / CMD / Git Bash / WSL，默认 PowerShell）、网页链接打开方式（应用内侧边栏 / 默认浏览器，默认侧边栏）、工作步骤展示档位（默认详细）、收起时机（默认回答结束后）、显示代码工作视图（默认关闭）。偏好保存在 `<home>/data/preferences.json`。
- **供应商**：以供应商为单位添加 / 编辑 / 删除。一个供应商共用 Base URL 与 API Key；编辑时维护它自己的模型列表（模型 ID、显示名称、可选推理等级、温度），也可以从上游获取模型列表。API Key 只显示掩码，不回显原文。
- **工具**：查看每个工具条目的风险等级与包含的工具名，启用 / 禁用，编辑参数。密钥类字段显示为「已配置 · ••••」，留空保存即沿用旧值，也可以用「清除」真正清空。这里还可以调整扩展工具搜索的每次加载数量，以及清除本会话的「始终允许」授权。

### 2.7 工作步骤

思考与工具调用按实际发生顺序交错排列，每行先显示动作标签和一行摘要：

```text
思考 · 需要先读取配置文件
读取 · config/settings.py
执行命令 · python -m unittest discover -s tests
```

- 点击行可以展开完整思考、工具参数或结果。
- **简洁**：动作标签与很短摘要。
- **标准**：动作标签、目标与短结果。
- **详细**：默认展开工具参数 / 结果，思考保持省略，点击可展开。
- **完全展开**：始终显示全部步骤、思考与输出，不自动收起。

收起时机可选「回答结束后」或「下次有新消息时」。手动展开的轮次不会被强制收起；完全展开模式下该选项不可用。开启「显示代码工作视图」后，每轮还会展示代码改动、命令以及文件 / 网页读取轨迹。

### 2.8 后台任务

右上角侧边栏按钮左侧显示「n个后台任务」，数字是当前保留的任务总数。点开后分为「运行中」和「已完成」两组，可以分别展开。每个任务依次显示使用的终端（`powershell` / `cmd` / `gitbash` / `wsl`）、命令、结果（运行中、退出码或已停止）和运行时间。

- 点击任务行展开详情：上方是完整命令，下方是终端输出。
- 运行中的任务有「停止」按钮，停止会结束整个进程树。
- 停止生成会一并停止当前会话里仍在运行的后台任务；其他会话的任务不受影响。
- 面板不显示表格边框和列标题；运行中的输出会定时刷新。
- `start_process` 默认在任务结束时通知会话。模型会收到一条内部续跑消息，继续检查结果、修复失败或汇报完成；长期运行的服务可以在启动时设置 `notify=False`，避免退出时自动续跑。

### 2.9 输入区工具

输入框左下角有「＋」按钮，展开后分为两组：

- **添加 → 文件**：选择文件后上传到当前工作区的 `.alembic/attachments/<会话ID>/`，文件会显示在输入框上方，可点右侧「×」取消导入。发送时附件路径会写入用户消息，Agent 可以用 `read_file`、`read_document`、`read_image` 读取。
- **命令 → 压缩 `compact`**：立即压缩当前会话的较早历史，并刷新会话摘要与上下文占用；正在生成时不可用。

输入框右侧的轮次统计和模型选择按钮之间有一个上下文占用圆环，右侧显示百分比。圆环在每轮完成、切换会话或手动压缩后更新；数据来自模型 API 的 token 用量，拿不到时退回本地估算。

## 3. 模型与供应商

一个供应商代表一组共用的 Base URL 与 API Key，下面可以挂多个模型。内置模板：

| 模板 | Base URL |
| --- | --- |
| 深度求索（DeepSeek） | `https://api.deepseek.com` |
| OpenAI | `https://api.openai.com/v1` |
| Anthropic Claude | `https://api.anthropic.com/v1` |
| 阿里云千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 硅基流动 | `https://api.siliconflow.cn/v1` |
| 自定义供应商 | 自行填写 |

所有供应商都通过 OpenAI 兼容的 `/chat/completions` 调用；Claude 使用 Anthropic 的兼容端点。自定义供应商必须填写 Base URL，新建供应商必须填写 API Key。

模型字段：

- **模型 ID**：上游实际接收的模型标识，以供应商文档为准。项目不内置默认模型。
- **显示名称**：界面上展示的名字，留空时使用模型 ID。
- **高级设置（每行左侧箭头）**：
  - 支持扩展思考：关闭后不发送 `reasoning_effort`，输入区的推理等级开关也会隐藏。
  - 支持图片输入：关闭后 `read_image` 返回 `vision_unavailable`，不会把图片塞给纯文本模型。
  - 上下文长度：默认按模型家族的最大上下文填写，可手动覆盖；压缩阈值按它推导。
  - 最大输出 tokens：默认按模型家族的输出上限填写，作为 `max_tokens` 传给模型。
  - 温度：默认 `0`。
  - 推理等级：可选集合为 `none / low / high / max`，运行时 `low / high / max` 映射为 `reasoning_effort`。

API Key 只保存在本机数据目录，接口返回时只给掩码。供应商删除后，它下面的模型一并移除；当前激活项会自动回退到其他可用模型。

## 4. Agent 执行与上下文

### 4.1 执行引擎

默认 `AGENT_ENGINE=tool_calling`，使用 LangChain `create_agent` 的模型原生工具调用：

1. 工具参数以结构化 `tool_calls` 返回，JSON Schema 由 `tools/registry.py` 派生。
2. 循环执行工具，把真实的 `ToolMessage` 作为观测交回模型。
3. 模型无法用文本伪造 Observation，动作也不会被文本解析悄悄丢弃。

文本 ReAct 保留为可选模式（`AGENT_ENGINE=react`，实现在 `agents/react.py`），并做了协议硬化：

1. 生成时带 `stop=["\nObservation:", …]`，模型写完 `Action Input` 必须停下，不能继续编造 Observation。
2. 一条消息只接受「一个 Action」或「一个 Final Answer」。
3. 模型自写 Observation、Action 与 Final Answer 混排、一条消息多个 Action，一律判为协议错误，回喂纠错提示后重试（有次数上限）。
4. 原始模型输出写入 debug 日志，解析决策有 warning 记录。

```text
Thought: I need to read the config first.
Action: read_file
Action Input: {"path": "config/settings.py"}
Observation: ...            # 只由系统追加
Thought: I now know the final answer
Final Answer: ...
```

两条路径共用同一份工具注册表，也复用审批、`todo_write`、`ask_user_question`、SSE 工具气泡、上下文压缩和会话持久化。结构化引擎会把「带工具调用的 AI 正文」派生成 `thought` 事件，ReAct 则直接展示中间 `Thought`，两条路径的工作步骤视图一致。最终回复使用用户的语言。

### 4.2 上下文机制

- **多轮记忆**：每轮把历史（长会话先压缩）交给 Agent，追问和指代可以延续。
- **token 统计**：优先使用模型 API 返回的 `usage`（包含系统提示词、工具 schema 与回复），拿不到时才退回本地字符估算。`/status` 会标明数据来源。
- **压缩阈值**：默认按模型窗口推导；上下文长度优先取当前模型的高级设置，未设置时回退 `MODEL_CONTEXT_WINDOW`。历史超过 `MAX_CONTEXT_MESSAGES` 条消息时也会触发，`MAX_CONTEXT_TOKENS` 可以覆盖 token 阈值。
- **压缩间隔**：两次自动压缩之间至少间隔 2 轮，避免压缩后立刻再次触发。
- **压缩方式**：较早对话压缩成以 `[Conversation summary]` 开头的英文摘要，保留最近 12 条消息；摘要提示词本身是英文，不额外要求摘要语言。摘要失败时降级为「省略更早消息 + 明确提示」，对话不中断。
- **用户可见原文**：压缩只替换模型上下文；会话文件同时保存 `display_messages`，历史接口和界面仍显示完整问答原文，摘要只给模型看。
- **工具输出预算**：单条工具结果超过 12000 tokens 会先截断再写入历史并留下标记，避免一次大输出撑爆窗口。
- **本地持久化**：会话历史保存在 `memory/<会话ID>.json`，进程重启或换终端后可以继续。文件里包含消息类型、工具调用参数与结果。
- **手动压缩**：输入区「＋ → 命令 → 压缩」或命令行 `/compact` 可以立即压缩；网页端同时刷新上下文占用圆环。
- **运行保护**：模型调用失败自动重试；单轮模型调用数与工具调用数都有上限。

命令行可以用 `/compact` 立即压缩、`/status` 查看占用与阈值、`/new` 开新会话。

### 4.3 项目上下文文件

工作区根目录可以放一份项目说明，Agent 会在每轮开始时读取并注入系统提示词，顺序为：

1. `ALEMBIC.md`（优先）
2. `AGENTS.md`
3. `CLAUDE.md`
4. `.cursorrules`

只取第一个匹配的文件。内容上限 20k 字符，超出时保留头尾并标注截断。包含「忽略先前指令」、索要密钥、隐藏 Unicode 等注入特征的文件会被标记为 `[BLOCKED]`，不会加载。项目说明适合写测试命令、目录约定和代码风格，不能覆盖安全、审批或命令策略，也不能覆盖用户的明确要求。

系统提示词会区分 `web` 与 `cli` 两种回复界面：网页版允许 Markdown，命令行版优先纯文本。运行时还会注入工作区、操作系统、首选终端、日期和 Git 分支。

## 5. 工具

### 5.1 工具注册表

工具清单只在 `tools/registry.py` 的 `TOOL_SPECS` 登记一次，网页设置面板、网页对话和命令行都从它派生。

| 条目 | 暴露给模型的工具 | 风险 | 默认 | 加载 |
| --- | --- | --- | --- | --- |
| `get_current_time` | `get_current_time` | 只读 | 开 | 常驻 |
| `list_timezones` | `list_timezones` | 只读 | 开 | 按需 |
| `get_weather` | `get_weather` | 只读（需和风天气凭据与 API Host） | 关 | 按需 |
| `file_read` | `list_dir` / `read_file` / `search_files` / `find_files` / `file_stat` | 只读 | 开 | 常驻 |
| `read_document` | `read_document` | 只读（PDF / Office / 文本） | 开 | 按需 |
| `read_image` | `read_image` | 只读（需支持图片输入的模型） | 开 | 按需 |
| `file_write` | `write_file` / `edit_file` / `make_dir` / `delete_file` / `move_file` / `copy_file` | 改工作区文件 | 开 | 常驻 |
| `apply_patch` | `apply_patch` | 改工作区文件 | 开 | 常驻 |
| `batch_edit` | `batch_edit` | 改工作区文件（原子多文件/多处替换） | 开 | 常驻 |
| `fetch_url` | `fetch_url` | 只读（默认禁内网） | 开 | 常驻 |
| `web_search` | `web_search` | 只读（默认 Bing，可换服务） | 开 | 常驻 |
| `download_file` | `download_file` | 写工作区文件（默认禁内网） | 开 | 按需 |
| `todo_write` | `todo_write` | 只读（会话状态） | 开 | 常驻 |
| `ask_user_question` | `ask_user_question` | 只读（等待用户回答） | 开 | 常驻 |
| `tools_search` | `tools_search` | 只读 | 开 | 常驻 |
| `run_command` | `run_command` | 执行本机命令 | 开 | 常驻 |
| `background_process` | `start_process` / `stop_process` | 执行本机命令 | 开 | 按需 |
| `process_inspect` | `list_processes` / `read_process_logs` / `wait_process` | 只读 | 开 | 按需 |
| `git_read` | `git_status` / `git_diff` | 只读 | 开 | 按需 |
| `git_commit` | `git_commit` | 执行本机命令 | 开 | 按需 |
| `mock_data` | `mock_lookup` | 读取自定义文本 | 关 | 按需 |

「风险」为写文件或执行命令的条目是否需要审批，由当前权限模式决定，见[第 6 节](#6-审批与安全边界)。

**基础工具**常驻模型上下文（前提是用户启用了对应条目）。**扩展工具**即使默认启用，也不会直接出现在模型 schema 中，需要先调用 `tools_search` 按能力搜索并加载，加载后同一轮即可调用。搜索默认加载 3 个、最多 10 个，可在「设置 → 工具 → 扩展工具搜索」里调整每次加载数量。用户在设置里禁用的扩展工具不会被搜索到，也不会进入模型 schema。

`tools_search` 本身被禁用时，扩展工具将无法加载。它是常驻的元工具，不参与扩展工具搜索。

`get_weather` 默认关闭，并且没有默认 API Host；需要先填写和风天气凭据与 API Host，再在「设置 → 工具」里启用。

### 5.2 文件与命令工具

所有文件与命令操作都以当前工作区为根目录。`..`、工作区外的绝对路径和符号链接逃逸都会被拒绝；判断方式是先 `resolve()` 再 `Path.relative_to()`，因此 `D:\work` 不会误认 `D:\workspace-evil` 为子目录。

| 工具 | 作用 |
| --- | --- |
| `list_dir(path=".")` | 列目录：目录带 `/`，文件带大小，超出上限说明省略数量 |
| `read_file(path, offset=1, limit=200)` | 带行号读取文本；单次上限 2000 行，大文件按 `offset` 分段，二进制文件直接拒绝 |
| `search_files(pattern, path=".", glob="", max_results=50)` | 按正则搜内容，可只搜 `*.py` 之类；自动跳过 `.git` / `.venv` / `node_modules` / `dist` / `build` 等目录 |
| `find_files(pattern, path=".", max_results=100)` | 按文件名或相对路径查找，支持 `*.py`、`**/*.test.ts` 等通配符 |
| `file_stat(path=".")` | 查看类型、大小、修改时间与文本行数 |
| `write_file(path, content)` | 整文件写入（必要时建目录），写入后回读大小校验 |
| `edit_file(path, old_string, new_string, replace_all=False)` | 精确匹配替换；匹配不唯一时报 `ambiguous_match` 并给出行号 |
| `batch_edit(edits)` | 原子批量替换：一次提交多个文件/多处；全部成功才落盘，任一失败或提交中断都会回滚，不留半套修改 |
| `make_dir(path)` | 创建目录（可多级） |
| `move_file(source, destination, overwrite=False)` | 移动 / 重命名；不能把目录移进自己的子目录 |
| `copy_file(source, destination, overwrite=False)` | 复制文件或目录 |
| `delete_file(path, recursive=False)` | 删除文件 / 目录；工作区根目录与 `.git` 受保护，非空目录需 `recursive=True` |
| `run_command(command, cwd="", timeout=0, terminal="", description="")` | 执行 shell 命令；输出实时推送，停止生成时结束进程树；`terminal` 可临时指定 powershell / cmd / gitbash / wsl；`description` 会作为审批卡主说明 |
| `start_process(command, cwd="", terminal="", description="", notify=True)` | 后台启动长任务（dev server / watch），立即返回进程 ID；`notify` 控制退出时是否自动续跑 |
| `list_processes()` / `read_process_logs(process_id, tail=200)` | 查看后台进程与日志尾部 |
| `wait_process(process_id, timeout=30, pattern="", tail=60)` | 等待进程退出，或日志中出现某个模式 |
| `stop_process(process_id)` | 停止进程及其子进程 |
| `tools_search(query, limit=0)` | 按能力搜索并加载扩展工具；加载后同轮可用 |

凭据路径额外受保护：`.ssh` / `.aws` / `.kube` / `.gnupg` / `.docker` 目录、`.netrc` / `.npmrc` / `.pypirc`、SSH 私钥以及 `.pem` / `.key` / `.p12` / `.pfx` / `.jks` / `.keystore` 不允许读取；这些凭据目录与 `.netrc` 也不允许写入。`.env` 可以写入但读不回来；应用自己的数据目录也不允许 Agent 访问。子进程只继承白名单环境变量，模型密钥不会出现在命令环境里。

`run_command` 在沙盒子进程中实时读取 stdout / stderr，两路输出都会推送 `tool_output` 事件，最终结果仍分开列出。停止生成、超时或客户端断开时会结束整个进程树；Windows 使用 Job Object 管理进程树，POSIX 原生后端尚未落地。

### 5.3 读后写

`write_file`、`edit_file`、`apply_patch` 和 `batch_edit` 都遵守「读后写」：

- 覆盖或修改已有文件前必须先 `read_file`，否则返回 `read_required`。
- 读过之后文件又被外部改动，返回 `stale_read`，要求重新读取，避免模型盲改。
- 只读过文件局部内容就整文件覆盖时，`write_file` 会成功写入，但在结果里提示可能丢失未读部分。
- 系统提示词要求优先用 `edit_file` 做局部替换，整文件重写才用 `write_file`。

`download_file` 不要求先读已有文件，但目标已存在时必须显式传 `overwrite=True`，并且可能进入审批。

### 5.4 文档读取

`read_document(path, offset=0, limit=0)` 抽取文档文字，支持：

- PDF（`pypdf`，最多 300 页）
- Word `.docx`（`python-docx`，保留标题与表格）
- Excel `.xlsx`（`openpyxl`，只读、取缓存值，单个工作表最多 300 行）
- PowerPoint `.pptx`（`python-pptx`，含演讲备注，最多 300 页）
- 常见纯文本（`.txt` / `.md` / `.csv` / `.json` / `.log` / `.xml` / `.yaml` 等）

返回首行给出格式与总字符数，长文档用 `offset` 按字符继续。旧式 `.doc` / `.xls` / `.ppt` 会返回明确错误；图片请改用 `read_image`。

### 5.5 网页抓取、搜索与下载

`fetch_url(url, max_chars=0)` 抓取网页或 HTTP 接口，返回最终 URL、状态、类型、标题与正文。默认拒绝回环与内网地址（防 SSRF）；需要访问本机服务时，在「设置 → 工具 → 网页抓取」里打开「允许内网地址」。重定向会逐跳校验，响应大小有上限，正文按工具输出预算截断。网页内容会标注为「外部数据，不可信」，模型不应执行其中的指令。

`web_search(query, max_results=0)` 返回标题、链接与摘要。默认使用免密钥的 Bing，也可以切换到 DuckDuckGo / Tavily / Brave / Serper / SearXNG。搜索结果同样按不可信外部数据标注。

`download_file(url, path="", overwrite=False)` 把 PDF、图片、压缩包等二进制内容流式下载到工作区，带 SSRF 校验、重定向逐跳校验与大小上限。失败不会留下 `.part` 半成品。它属于写工作区文件，是否弹审批取决于权限模式。

`read_image(path, question="")` 把图片交给当前模型做视觉理解并转写文字。支持 PNG / JPEG / GIF / WebP / BMP / TIFF，单张上限 8 MB。只有支持图片输入的模型可用；纯文本模型会返回 `vision_unavailable` / `vision_failed`，此时需要切换到支持视觉的模型。

### 5.6 补丁、Git 与后台进程

- `apply_patch(patch)`：应用标准 unified diff，支持多文件、多 hunk、新建与删除。仍然遵守读后写；适合跨文件重构，只改一处时用 `edit_file` 更直接。
- `batch_edit(edits)`：一次提交多个精确替换。所有替换先在内存中校验；任一 `old_string` 缺失、重复或文件过期，整批不落盘。提交阶段失败会回滚已写文件。`edits` 每项为 `{path, old_string, new_string, replace_all?}`。
- `git_status()` / `git_diff(path="", staged=False, context=3)`：查看分支、改动与 unified diff。
- `git_commit(message, files=None)`：只提交已暂存内容；传 `files` 时先 `git add` 这些路径，不会静默执行 `git add -A`。
- `start_process` / `list_processes` / `read_process_logs` / `wait_process` / `stop_process`：后台运行 dev server、watch 或长脚本。启动与停止属于执行命令，是否审批取决于权限模式。日志写到数据目录的 `logs/processes/`，停止时会结束子进程。任务退出时通过 `/api/processes/events` 主动推送完成事件，前端据此刷新任务面板并触发内部续跑；`wait_process` 仍保留给需要显式等待的场景，但常规后台任务不必用它挂住整轮。

### 5.7 任务清单与提问

- `todo_write(todos)`：整体更新本会话的任务清单，状态为 `pending` / `in_progress` / `completed`，最多 50 条。同一时间只允许一条 `in_progress`。网页端在对话顶部显示进度，刷新后从 `/api/sessions/{id}/todos` 恢复。
- `ask_user_question(question, options, allow_free_text=True)`：需要用户补充信息或做选择时阻塞等待。网页端出现问答卡片（选项按钮 + 文本框），命令行在终端提示；最多 6 个选项。超时或流断开返回 `question_timeout` / `question_cancelled`，不会假装已回答。

### 5.8 时间与时区

`get_current_time(timezone_name="local")` 支持四类输入（大小写、空格不敏感）：

| 写法 | 示例 |
| --- | --- |
| 本地时间 | `local`、`本地`、`本机`、`系统时间` |
| IANA 时区 | `Asia/Shanghai`、`UTC`、`America/New_York`、`asia/shanghai` |
| 城市 / 省份 / 国家 | `北京`、`上海`、`纽约`、`东京`、`日本`、`美国西部` |
| UTC 偏移 | `UTC+8`、`GMT-5`、`+08:00`、`东八区`、`西五区` |

返回示例：

```text
地区：北京 → Asia/Shanghai
时间：2026-11-04 09:21:11（星期三）
时区：CST，UTC+08:00，夏令时：否
ISO 8601：2026-11-04T09:21:11+08:00
Unix 时间戳：1793755271
```

无法识别时返回以 `Error:` 开头的说明，并附带相近候选。`list_timezones(keyword)` 可以列出支持的地区与 IANA 名称。

Windows 默认不带 IANA 时区数据库，Python 的 `zoneinfo` 需要 `tzdata` 包（已写入 `pyproject.toml`，`uv sync` 会自动安装）。如果运行环境缺少 `tzdata`，工具会退回内置常用时区偏移表，并在结果中以「注意：」标注未应用夏令时；此时有夏令时的地区可能相差 1 小时。

### 5.9 新增工具

工具清单只有一处：`tools/registry.py` 的 `TOOL_SPECS`。加一个条目即可，前端无需改动：

```python
"translate_text": ToolSpec(
    name="translate_text",
    label="文本翻译",
    description="把一段文本翻译成目标语言。",
    fields=(
        ToolField("TRANSLATE_API_KEY", "API Key", secret=True),
        ToolField("TRANSLATE_ENDPOINT", "接口地址", placeholder="https://…"),
        ToolField("TRANSLATE_MODE", "模式", kind="select",
                  options=(("fast", "快速"), ("deep", "深度")), default="fast"),
        ToolField("TRANSLATE_LIMIT", "单次字数", kind="number", default="2000"),
    ),
    tool_names=("translate_text",),
    builder=_build_translate,   # (config, ctx) -> list[BaseTool]
),
```

设置面板会自动出现这张卡片和对应控件；保存后下一轮对话里，`builder` 拿到的 `config` 就是这些值（缺省回落到 `.env` 同名变量与 `ToolField.default`）。

字段形态：`text`（默认，`multiline=True` 变多行）、`number`（保存时校验数字）、`select`（必须给 `options`）、`switch`（存 `"true"` / `"false"`）。`secret=True` 不回显、留空保持原值、可用「清除」真正清空；`required=True` 只在界面标星，是否允许为空由工具自己判断。设 `risk=RISK_WRITE` / `RISK_EXEC` 会把工具纳入审批判定，是否弹窗取决于权限模式。

内置的 `mock_data` 是这条链路的样板：在「设置 → 工具」里启用「模拟数据」并填一段文字，Agent 调用 `mock_lookup` 就会原样拿到它（未填写时返回 `Error: [missing_config]`）。

新增或删除工具时，同步更新本节表格，避免文档与注册表脱节。

### 5.10 工具错误契约

所有工具失败时都返回以 `Error:` 开头的字符串，而不是抛异常（抛异常会中断整轮对话）：

```text
Error: [read_required] 写入前必须先读取 a.txt 的现有内容。
请先调用 read_file 查看原文，再决定是整文件覆盖（write_file）还是局部替换（edit_file）。
```

`[code]` 是稳定的机读错误码，例如 `not_found`、`path_escape`、`protected_path`、`read_required`、`stale_read`、`ambiguous_match`、`bad_pattern`、`timeout`、`auth_config`、`blocked_host`、`unsupported_content`、`missing_dependency`、`tool_not_activated`、`question_timeout`、`unknown_process`。

统一前缀让模型能稳定判断「这次调用失败了」；如果不同工具的大小写不一致，模型可能漏判，然后在错误的前提上继续推理。

命令的非零退出码不算工具错误——命令确实执行了，只是结果失败，因此返回 `退出码：N（失败）` 加上 stderr，由模型决定下一步。

## 6. 审批与安全边界

### 6.1 权限模式

`read` 类工具只读，`write` 类会改工作区文件，`exec` 类会执行本机命令。是否在调用前询问用户，由权限模式决定：

| 权限模式 | 只读工具 | 写工作区文件 | 执行命令 |
| --- | --- | --- | --- |
| 只读 | 直接执行 | 直接拒绝 | 直接拒绝 |
| 全部审批 | 审批 | 审批 | 审批 |
| 工作区内修改（默认） | 直接执行 | 直接执行 | 审批 |
| 完全权限 | 直接执行 | 直接执行 | 直接执行 |

完全权限只关闭审批，不关闭命令策略：不可恢复的高危命令仍会被拒绝。只读模式在审批之前就拒绝写文件与执行命令，即使还有待处理的审批也不会放行。

### 6.2 审批选项

每次确认提供三种选择：

| 选择 | 作用范围 |
| --- | --- |
| 允许本次 | 只放行这一次调用 |
| 始终允许 | 本会话内记住「工具 + 对象」（命令原文 / 文件路径 / 目标），之后同类调用自动放行 |
| 拒绝 | 工具不执行，返回 `Error: [approval_denied]`，模型应换方案而不是重试 |

「始终允许」按会话保存：命令行切换会话时清空；网页端各会话互不影响，切回原会话仍保留，直到在「设置 → 工具」里清除。授权键按操作对象区分：`write_file` 与 `edit_file` 共用同一个文件键；删除、移动、复制、建目录、下载各自独立，避免「允许修改」被扩大成「允许删除」。网页端可以在「设置 → 工具 → 清除本会话『始终允许』」撤销；命令行用 `/approvals` 查看，`/approvals clear` 清除。

网页端审批卡会先给一句人话说明，命令类请求再拆成分步说明。原始命令、写入内容前 800 字符、`edit_file` 的 `-旧 / +新` 都放在可折叠区域里，默认展开；命令原文可滚动，上限 8000 字符。危险命令额外显示风险原因。`run_command` / `start_process` 的说明优先取模型自填的 `description`；模型没填时由 `tools/command_brief.py` 离线翻译命令。

命令行提示为 `[y] 允许本次 / [a] 始终允许 / [n] 拒绝`，非交互输入一律按拒绝处理。

### 6.3 命令策略

审批之外还有一层命令策略：

- **直接拒绝**：格式化磁盘、管理磁盘 / 分区、删除系统盘根目录、注销 WSL 发行版、破坏引导记录、删除注册表根键、关机 / 重启、擦除磁盘空闲空间、Base64 编码的长命令等不可恢复操作。返回 `Error: [blocked_by_policy]`，不弹审批，也不能用「始终允许」绕过。
- **风险提示**：递归删除、强制推送、硬重置、结束进程、修改权限、远程下载后执行、创建计划任务 / 服务 / 启动项、访问凭据文件等可恢复但危险的操作，会在审批卡片上说明风险原因。

策略按命令文本匹配，会做小写化与空白折叠。它不是完整的 shell 解释器，也无法阻止刻意混淆的命令；它是审批之外的补充，真正隔离由下一节的命令沙盒提供。

### 6.4 命令沙盒

命令不会以桌面用户身份直接执行。Windows 后端为命令创建独立的低权限本地用户，并通过 ACL、Job Object 和按用户匹配的 Windows Firewall 约束：

- 首次使用需要以管理员身份运行一次 `scripts\setup-sandbox.ps1`；它创建 `AlembicSandboxOffline` / `AlembicSandboxOnline` 两个标准用户、防火墙规则和 DPAPI 保护的凭据文件。
- 应用日常运行不需要管理员权限，也不以管理员身份运行。
- 工作区授予沙盒用户 `Modify`；应用数据目录、`.ssh` / `.aws` / `.kube` / `.gnupg` / `.docker`、`.netrc` / `.npmrc` / `.pypirc`、私钥和工作区 `.env` 加 deny ACE。
- 子进程自动加入 Job Object；停止生成、超时、客户端断开都会结束整个进程树。
- 网络策略：
  - `offline`：完全断网。
  - `allowlist`：沙盒用户只能连接本机 `127.0.0.1:47831` 的 HTTP CONNECT 代理，代理只放行列出的域名和端口；未列出的域名直接返回 403。
- 离线 / 允许列表切换在「设置 → 通用 → 命令沙盒」中完成；允许域名保存在 `data/sandbox.json`。
- 沙盒不可用（未初始化、代理端口被占用、工作区不是本地 NTFS 等）时，命令工具返回 `Error: [sandbox_unavailable]`，不会回退到宿主机执行。
- Windows 沙盒下不能选择 WSL 终端：WSL 由独立服务启动，不能保证继承沙盒用户和 Job Object；需要 WSL 时使用独立的 Linux 沙盒后端。
- 文件工具仍使用工作区路径校验和应用层凭据保护；它们不是通过这个子进程沙盒执行的。
- 当前原生后端只覆盖 Windows；Linux/macOS 在对应后端落地前，命令工具同样失败关闭，不会退回宿主机执行。

```powershell
# 管理员 PowerShell，在仓库根目录执行一次
.\scripts\setup-sandbox.ps1
.\scripts\setup-sandbox.ps1 -Verify
# 卸载
.\scripts\setup-sandbox.ps1 -Uninstall
```

### 6.5 超时与失败关闭

- 等待审批超过 `APPROVAL_TIMEOUT` 秒按拒绝处理，默认 600 秒。
- 等待提问超过 `QUESTION_TIMEOUT` 秒返回未回答，默认 600 秒。
- 客户端中途断开时，正在等待的审批立即按拒绝收尾，提问返回取消，不把工具线程挂在后台。
- 审批通道出错（异常、EOF、无人值守）一律视为拒绝，绝不默认放行。
- `APPROVAL_ENABLED=0` 只关闭弹审批；只读边界与高危命令策略仍然生效。

拒绝只影响这一次调用，不会中断对话：模型收到 `Error: [approval_denied]` 后可以解释原因、改用只读方式确认现状，或等你放行。

实现上没有使用 LangChain 自带的 `HumanInTheLoopMiddleware`。它基于 LangGraph 的 `interrupt()`，需要 checkpointer 并把一轮对话拆成「暂停 + resume」；这里改用 `wrap_tool_call` 拦截 + 同步等待回执，与「一次 SSE 流跑完一轮」的结构保持一致。

## 7. 命令行

```powershell
.\.venv\Scripts\python.exe main.py "北京现在几点？"         # 单轮提问，接着当前会话聊
.\.venv\Scripts\python.exe main.py                         # 交互模式（多轮上下文）
.\.venv\Scripts\python.exe main.py --debug "东京现在几点？"  # 额外打印本轮消息轨迹
.\.venv\Scripts\python.exe main.py --session work "..."     # 指定会话，历史跨进程复用
.\.venv\Scripts\python.exe main.py --new                    # 清空当前会话后再开始
.\.venv\Scripts\python.exe main.py --list-sessions          # 列出已保存的会话
```

命令行默认工作区是当前目录，可用 `WORKSPACE_DIR` 指定。文件类工具直接读写所在目录；审批默认使用「工作区内修改」语义，命令执行前会在终端询问。命令同样经过沙盒；未运行 `setup-sandbox.ps1` 时命令工具会返回 `sandbox_unavailable`。

命令行与网页端有两处独立配置：

- 模型：命令行读取 `DEEPSEEK_API_KEY` 与 `MODEL_NAME`，不使用网页端的供应商列表。没有默认模型名，两个变量都必须配置。
- 工具：命令行使用注册表里默认启用的条目，不读取网页端的工具开关和参数；需要凭据的工具仍可从 `.env` 的同名变量回退。

### 交互命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示全部命令 |
| `/new [会话ID]` | 开启新会话（清空历史，不填 ID 则按时间生成） |
| `/session <会话ID>` | 切换到已有会话，继续之前的历史 |
| `/sessions` | 列出已保存的会话 |
| `/history [n]` | 查看当前会话最近 n 条消息 |
| `/todos` | 查看当前会话的任务清单 |
| `/approvals` | 查看本会话的「始终允许」；加 `clear` 全部清除 |
| `/compact` | 立即压缩上下文（把较早对话生成摘要） |
| `/status` | 查看轮次、消息数、token 估算与压缩阈值 |
| `/debug` | 切换调试模式（打印消息轨迹） |
| `/exit` / `/quit` | 退出 |

## 8. 数据、配置与接口

### 8.1 数据目录

配置与会话不在安装目录里，默认放在用户级目录（安装到 `Program Files` 也能写）：

| 平台 | 默认位置 |
| --- | --- |
| Windows | `%APPDATA%\Alembic\` |
| macOS | `~/Library/Application Support/Alembic/` |
| Linux | `$XDG_DATA_HOME/Alembic/`（默认 `~/.local/share/Alembic/`） |

可用 `ALEMBIC_HOME` 覆盖整个目录。首次启动时，如果目标目录为空而仓库里还有 `data/`、`memory/`，会把它们复制过去（只复制、不删除，原目录保留可回退）；之后再改仓库里的文件不会再影响运行中的应用。

| 文件 | 内容 |
| --- | --- |
| `<home>/data/models.json` | 供应商列表、各供应商的模型与当前激活模型 |
| `<home>/data/tools.json` | 工具启用状态与参数 |
| `<home>/data/workspace.json` | 命名工作区列表与当前激活项 |
| `<home>/data/preferences.json` | 权限模式、终端偏好、链接打开方式、工作步骤展示、收起时机、代码工作视图 |
| `<home>/data/sandbox.json` | 沙盒网络模式、允许域名与端口 |
| `<home>/data/sandbox-users.json` | 沙盒本地用户的 DPAPI 加密凭据（仅当前用户可读） |
| `<home>/data/sandbox-setup.json` | 沙盒初始化标记 |
| `<home>/memory/*.json` | 会话历史 |
| `<home>/memory/todos/*.json` | 会话级任务清单 |
| `<home>/logs/processes/` | 后台进程日志（`MEMORY_DIR` 覆写时跟随其父目录） |
| `<workspace>/.alembic/attachments/<会话ID>/` | 输入区导入的附件；工作区内的隐藏目录 |

网页端保存的 API Key 与密钥参数只写入这些本地文件；接口只返回掩码。`python -m web` 启动时会打印实际数据目录。`DATA_DIR`、`MEMORY_DIR`、`MODELS_FILE`、`TOOLS_FILE`、`WORKSPACE_FILE`、`PREFERENCES_FILE` 可以分别覆盖对应路径。

### 8.2 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ALEMBIC_HOME` | 平台用户目录 | 整个数据根目录，见上一节 |
| `DATA_DIR` | `<home>/data` | 网页配置目录 |
| `MEMORY_DIR` | `<home>/memory` | 会话历史目录 |
| `PREFERENCES_FILE` | `<data>/preferences.json` | 通用设置文件 |
| `MODELS_FILE` / `TOOLS_FILE` / `WORKSPACE_FILE` | `<data>/models.json` 等 | 更细粒度的配置文件覆盖 |
| `WORKSPACE_DIR` | 当前目录 | 命令行默认工作区 |
| `SESSION_ID` | `default` | 命令行默认会话 ID |
| `MODEL_NAME` | （未设置） | 命令行模型名；与 API Key 同时设置时也会在网页端生成供应商 |
| `DEEPSEEK_API_KEY` | （未设置） | 命令行模型 API Key；网页端也可以用初始化向导配置其他供应商 |
| `TERMINAL_PREFERENCE` | `powershell` | 命令行默认终端 |
| `AGENT_ENGINE` | `tool_calling` | `tool_calling` 使用模型原生工具调用；`react` 走文本 ReAct |
| `MODEL_CONTEXT_WINDOW` | `200000` | 模型上下文窗口（token） |
| `COMPACT_TRIGGER_RATIO` | `0.7` | 触发压缩的窗口占比 |
| `COMPACT_RESERVE_TOKENS` | `12000` | 为回复与摘要请求预留的 token |
| `MAX_CONTEXT_TOKENS` | `0` | 绝对阈值覆盖；`0` 表示按窗口比例推导 |
| `MAX_CONTEXT_MESSAGES` | `60` | 触发压缩的消息条数阈值 |
| `KEEP_RECENT_MESSAGES` | `12` | 压缩后保留的最近消息数 |
| `MAX_TOOL_OUTPUT_TOKENS` | `12000` | 单条工具输出写入历史的预算 |
| `MAX_TOOL_OUTPUT_CHARS` | `48000` | 单个工具返回的字符上限（超出截断并标注） |
| `EXECUTION_MODE` | `adaptive` | `conservative` / `adaptive` / `aggressive`，决定默认调用上限 |
| `MAX_MODEL_CALLS` | `20` | 单轮模型调用上限；保守 8 / 自适应 20 / 激进 40 |
| `MAX_TOOL_CALLS` | `50` | 单轮工具调用上限；保守 15 / 自适应 50 / 激进 100 |
| `MODEL_MAX_RETRIES` | `3` | 模型调用失败重试次数 |
| `APPROVAL_ENABLED` | `1` | 设为 `0` 关闭弹审批（命令策略仍生效） |
| `APPROVAL_TIMEOUT` | `600` | 等待人确认的秒数，超时按拒绝处理 |
| `QUESTION_TIMEOUT` | `600` | `ask_user_question` 等待回答的秒数 |
| `COMMAND_TIMEOUT` | `300` | `run_command` 默认超时（秒） |
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `8000` | 后端监听地址 |
| `FRONTEND_URL` | `http://127.0.0.1:5173` | 前端开发地址（日志与 `/` 返回里展示） |
| `CORS_ORIGINS` | 本地 5173 / 4173 | 允许跨域的前端来源，逗号分隔 |
| `ALEMBIC_TOKEN` | 未设置 | 设置后 API 需要 Bearer 或 Cookie 令牌 |
| `ALEMBIC_PORT` | 未设置 | 覆盖监听端口，`0` 由系统分配 |
| `QWEATHER_KEY_ID` / `QWEATHER_PROJECT_ID` / `QWEATHER_DEVELOPER_ID` / `QWEATHER_PRIVATE_KEY` / `QWEATHER_API_HOST` | 未设置 | 和风天气凭据；网页端建议在「设置 → 工具」填写 |

前端自己的变量写在 `frontend/.env.local`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000` | Vite 开发 / 预览服务器把 `/api` 代理到哪里 |
| `VITE_API_BASE_URL` | 空 | 直连后端地址；留空表示请求同源 `/api` |

设置 `ALEMBIC_TOKEN` 后，浏览器首次可以用 `?token=<令牌>` 打开，前端会把令牌存入 sessionStorage 并改用 `Authorization: Bearer` 请求头。

### 8.3 重置配置

[scripts/reset-config.ps1](scripts/reset-config.ps1) 会清除当前与默认数据目录下的 `data/`，以及仓库里会被首次启动复制过去的示例 `data/`。默认不会改动代码、`.env`、工作区文件或会话历史。

```powershell
# 双击 scripts\reset-config.cmd 也可以
.\scripts\reset-config.ps1 -DryRun                 # 只列出将删除的目录
.\scripts\reset-config.ps1 -Backup                 # 删除前备份到 %TEMP%
.\scripts\reset-config.ps1                         # 交互确认后删除
.\scripts\reset-config.ps1 -IncludeSessions -Force # 连会话历史一起清空
```

- 默认保留 `memory/`；`-IncludeSessions` 会一起清空会话历史。
- `-Backup` 的备份包含 API Key，请妥善保管。
- 执行前请停止后端 / 前端，避免运行中的进程把配置重新写回。
- `-KeepRepoSeed` 可保留仓库里的示例 `data/`（以及带 `-IncludeSessions` 时的 `memory/`）。
- 如果 `.env` 或当前终端同时设置了 `DEEPSEEK_API_KEY` 与 `MODEL_NAME`，首次启动会自动生成供应商，不会进入初始化向导；需要看向导时请临时注释 `MODEL_NAME`。

### 8.4 HTTP API

后端只提供 JSON 与 SSE。常用接口：

- `GET /api/health`、`GET /api/bootstrap`
- `GET /api/providers`、`POST /api/providers`、`DELETE /api/providers/{id}`
- `POST /api/providers/{id}/activate`、`PATCH /api/providers/{id}/models/{model_id}/thinking`
- `POST /api/providers/fetch`
- `GET /api/tools`、`PUT /api/tools/{name}`
- `GET /api/preferences`、`PUT /api/preferences`
- `GET /api/sandbox/status`、`GET /api/sandbox/config`、`PUT /api/sandbox/config`
- `GET /api/workspaces`、`POST /api/workspaces`、`PATCH /api/workspaces/{id}`、`DELETE /api/workspaces/{id}`
- `POST /api/workspaces/{id}/activate`、`GET /api/workspaces/browse`
- `GET /api/workspace/files`、`GET /api/workspace/file`
- `GET /api/sessions`、`POST /api/sessions`、`DELETE /api/sessions/{id}`
- `GET /api/sessions/{id}/messages`、`GET /api/sessions/{id}/todos`
- `POST /api/sessions/{id}/compact`、`POST /api/sessions/{id}/cancel`
- `POST /api/sessions/{id}/attachments`（原始字节体，`filename` 与 `workspace_id` 走查询参数）
- `POST /api/chat`（SSE 流式，支持 `attachments` / `internal`）
- `GET /api/processes`、`GET /api/processes/{id}`、`POST /api/processes/{id}/stop`
- `GET /api/processes/events`（后台任务状态 SSE）
- `POST /api/approvals/{request_id}`、`GET /api/approvals`、`DELETE /api/approvals`
- `POST /api/questions/{question_id}`

`GET /docs` 提供 FastAPI 的交互式接口文档；启用访问令牌时需要先完成授权。

## 9. 开发、测试与打包

### 9.1 目录结构

```text
main.py                 命令行入口（argparse + ChatCLI）
run_backend.py          PyInstaller 入口，等价于 python -m web
agents/
  agent.py              模型 / 工具 / 引擎装配
  approval.py           审批策略、中间件、终端提问器
  context.py            会话、上下文压缩、会话持久化
  react.py              可选文本 ReAct 循环
  tool_visibility.py    扩展工具可见性过滤
config/
  settings.py           环境变量与路径解析
  storage.py            数据目录初始化与迁移
  store.py              供应商 / 工具 / 工作区 / 偏好持久化
prompts/
  system.py             分层系统提示词
  context.py            项目上下文文件加载
  react.py              ReAct 格式与工具目录模板
tools/
  registry.py           工具清单唯一事实来源
  catalog.py            基础 / 扩展工具目录与 tools_search
  files.py documents.py images.py
  fetch.py search.py download.py
  patch.py git.py processes.py shell.py
  todo.py questions.py time.py weather.py
  paths.py command_policy.py command_brief.py env.py
  winjob.py             Windows Job Object：按进程树停止命令
  sandbox/
    base.py             沙盒策略 / 状态 / 进程协议
    windows.py          专用本地用户、ACL、Job Object、命令启动
    proxy.py            本机 HTTP CONNECT 代理与域名允许列表
    config.py           data/sandbox.json 配置
web/
  server.py             路由、SSE、审批、工作区、供应商、进程视图
  runtime.py            按当前设置装配 Agent
  process_events.py     后台任务完成事件的分发
  auth.py approvals.py questions.py
frontend/
  src/                  Vue 界面、Pinia 状态、Markdown 与 Diff 渲染
  vite.config.ts        /api 代理与 5173 / 4173 端口
scripts/
  dev.ps1 dev.cmd       一键启动开发环境
  setup-sandbox.ps1     管理员一次性初始化命令沙盒
  verify-sandbox.ps1    校验沙盒用户、防火墙与配置
  reset-config.ps1      重置配置
desktop/                Electron 外壳（暂未适配当前前端）
packaging/              PyInstaller 后端打包
tests/                  *_test.py，全部离线
```

### 9.2 测试

```powershell
# 后端：467 个离线测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py" -v

# 前端：类型检查 + 生产构建
cd frontend
npm run build
```

测试覆盖时间工具、会话持久化、上下文压缩、命令行交互、网页接口、工具注册表与错误契约、ReAct 循环、文件工具、原子批量编辑与回滚、文档抽取、网页抓取、任务清单、提问中继、后台进程、实时命令输出与取消、沙盒策略与域名匹配、附件与上下文压缩接口、工具审批。全部离线运行，不调用真实模型。原生 Windows 沙盒的集成探针需要管理员初始化后单独运行，见 `scripts/verify-sandbox.ps1`。当前有一个依赖宿主终端的 Git Bash 用例在环境不具备时会跳过。

### 9.3 桌面外壳与打包

桌面版尚未适配当前的前后端分离结构：

- `desktop/main.js` 启动后端后加载后端根地址，而根地址现在只返回 API JSON，不会显示对话界面。
- `packaging/build.ps1` 可以构建后端可执行文件，产物只提供 `/api`，不包含网页资源。
- 恢复桌面版时，需要让外壳加载 `frontend/dist`（开发时启动 Vite），并重新设计 sidecar 的静态资源路径。

浏览器使用不受影响。Electron 环境坑与打包细节保留在 [desktop/README.md](desktop/README.md) 和 [HANDOVER.md](HANDOVER.md)。前端自己的说明见 [frontend/README.md](frontend/README.md)。
