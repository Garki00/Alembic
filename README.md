# SmartAgentDemo

一个基于 LangChain + DeepSeek 的小型 Agent 示例，具备多轮上下文、会话持久化、
自动上下文压缩，以及可读写工作区文件的工具集。

前端是 **Vue 3 + Vite + TypeScript**（`frontend/`），后端是 **FastAPI**（`web/`），
两者通过 `/api` 通信，可以分别启动、分别部署。本次重构后暂不构建 Electron 应用，
先保证本地浏览器可直接使用。

> 接手开发 / 让其他智能体继续改这个项目，请先读 **[HANDOVER.md](HANDOVER.md)**：
> 里面有架构图、数据流、不可破坏的约定、环境坑和已知问题。

## 内置工具

工具清单只在 `tools/registry.py` 登记一次，网页设置面板、网页对话与命令行都从它派生。

| 条目 | 暴露给模型的工具 | 风险 | 默认 |
| --- | --- | --- | --- |
| `get_current_time` | `get_current_time` | 只读 | 开 |
| `list_timezones` | `list_timezones` | 只读 | 开 |
| `get_weather` | `get_weather` | 只读（需 QWeather 凭据） | 开 |
| `file_read` | `list_dir` / `read_file` / `search_files` | 只读 | 开 |
| `file_write` | `write_file` / `edit_file` | 改工作区文件 | 开 |
| `run_command` | `run_command` | 执行本机命令 | 开（需确认） |
| `mock_data` | `mock_lookup` | 读取自定义文本 | 关 |

`risk` 为 `write` / `exec` 的条目在真正执行前需要人工确认，见下文「工具审批」。

## 环境准备

```powershell
uv sync              # 按 uv.lock 创建/更新 .venv 并安装 Python 依赖

cd frontend
npm install          # 安装 Vue / Vite 依赖（首次）
```

也可以直接运行 `.\scripts\dev.ps1`：它会检查依赖、启动后端与前端，并打开浏览器。

在项目根目录的 `.env` 中填入以下变量：

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 模型 API Key（必填） |
| `QWEATHER_KEY_ID` / `QWEATHER_PROJECT_ID` / `QWEATHER_DEVELOPER_ID` / `QWEATHER_PRIVATE_KEY` | 和风天气 JWT 凭据 |

## 网页对话工作区（前后端分离）

后端只提供 `/api`，界面在 `frontend/`（Vue 3 + Vite）。开发时两个进程分别启动：

```powershell
# 终端 1：后端 http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m web

# 终端 2：前端 http://127.0.0.1:5173
cd frontend
npm run dev
```

或者一条命令同时启动两者（首次会自动安装前端依赖，并打开浏览器）：

```powershell
.\scripts\dev.ps1
```

浏览器打开 **http://127.0.0.1:5173**。后端根地址 `http://127.0.0.1:8000`
只返回 API 说明，不会再直接展示页面。

Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`；端口不同时在
`frontend/.env.local` 设置 `VITE_BACKEND_URL`。后端允许的前端来源由
`CORS_ORIGINS` 控制，默认包含 `127.0.0.1` / `localhost` 的 5173 与 4173 端口。

界面采用「纸白 + 薄荷绿」的编辑排版风格：

- **左侧栏**：按工作区分组。每个工作区行悬停后出现「＋ 新建对话」与「⋯ 菜单」
  （重命名 / 删除），顶部有搜索框（同时搜工作区名与对话标题），左上角齿轮进入设置。
  添加工作区走目录浏览，每个工作区可单独起名。
- **对话窗口**：流式输出、工具调用气泡（如 `get_current_time`）、Markdown 渲染、
  上下文占用提示、停止生成。会话归属它的工作区，切换工作区不丢历史。
- **模型切换**：输入框右下角显示当前模型，点开即可切换，底部还有「管理模型…」入口。
- **设置面板**（唯一入口是左上角齿轮）：
  - **模型**：添加 / 编辑 / 删除模型，设置名称、协议（DeepSeek / OpenAI 兼容）、
    模型名、Base URL、API Key、Temperature；「导入全部上游模型」会把
    `{base_url}/models` 列出的模型一次性加入可选列表（已存在的跳过）。
  - **工具**：查看现有工具、风险等级与包含的具体工具名，启用 / 禁用；参数
    （如 `QWEATHER_*`）可直接修改，保存后下一轮对话立即生效。密钥类字段**不回显**，
    显示为「已配置 · ••••CEH6」，留空保存即沿用旧值，另有「清除」按钮可真正清空。

### 数据目录

配置与会话**不在安装目录里**，默认放在用户级目录（安装到 `Program Files` 也能写）：

| 平台 | 默认位置 |
| --- | --- |
| Windows | `%APPDATA%\SmartAgent\` |
| macOS | `~/Library/Application Support/SmartAgent/` |
| Linux | `$XDG_DATA_HOME/SmartAgent/`（默认 `~/.local/share/SmartAgent/`） |

可用 `SMART_AGENT_HOME` 覆盖整个目录。首次启动时，如果目标目录为空而仓库里还有旧的
`data/`、`memory/`，会把它们**复制**过去（只复制、不删除，原目录保留可回退）；
之后再改仓库里的文件不会再影响运行中的应用。

| 文件 | 内容 |
| --- | --- |
| `<home>/data/models.json` | 模型列表与当前激活模型（API Key 仅本地保存，接口只返回掩码） |
| `<home>/data/tools.json` | 工具启用状态与参数（密钥仅在本地保存，接口只返回掩码） |
| `<home>/data/workspace.json` | 命名工作区列表与当前激活项（旧格式会自动迁移） |
| `<home>/memory/*.json` | 会话历史 |

`python -m web` 启动时会打印实际数据目录，可直接确认。`DATA_DIR` / `MEMORY_DIR`
仍可作为更细粒度的覆盖。

接口一览：`/api/bootstrap`、`/api/chat`（SSE 流式）、`/api/models`（CRUD / activate /
fetch）、`/api/tools`、`/api/workspaces`（CRUD / activate / browse）、`/api/sessions`、
`/api/approvals`。

## 文件与命令工具

所有文件 / 命令操作都以**当前工作区**为根目录（网页在左侧栏选，命令行默认当前目录，
可用 `WORKSPACE_DIR` 指定）。`..`、绝对路径与符号链接逃逸都会被拒绝，判断方式是
先 `resolve()` 再 `Path.relative_to()`，因此 `D:\work` 不会误认 `D:\workspace-evil` 为子目录。

| 工具 | 作用 |
| --- | --- |
| `list_dir(path=".")` | 列目录：目录带 `/`，文件带大小，超出上限说明省略数量 |
| `read_file(path, offset=1, limit=200)` | 带行号读取文本；大文件按 `offset` 分段，二进制文件直接拒绝 |
| `search_files(pattern, path=".", glob="", max_results=50)` | 按正则搜内容，可只搜 `*.py` 之类，自动跳过 `.git` / `.venv` / `node_modules` |
| `write_file(path, content)` | 整文件写入（必要时建目录） |
| `edit_file(path, old_string, new_string, replace_all=False)` | 精确匹配替换；匹配不唯一时报 `ambiguous_match` 并给出行号 |
| `run_command(command, cwd="", timeout=0)` | 执行 shell 命令，返回退出码 + stdout / stderr |

两条硬约束：

- **读后写**：覆盖或修改已有文件前必须先 `read_file`，否则返回 `read_required`；
  读过之后文件又被外部改动，返回 `stale_read` 要求重读，避免模型盲改。
- **先局部后整体**：系统提示词要求优先用 `edit_file` 做局部替换，整文件重写才用
  `write_file`。

`run_command` 会在本机真实执行命令，没有沙箱，因此每次执行前都要确认（见下文
「工具审批」）。它默认开启；如果不需要，可在设置 → 工具里关掉。

## 新增工具（只改 Python）

工具清单只有一处：`tools/registry.py` 的 `TOOL_SPECS`。加一个条目即可，前端无需改动：

```python
"web_search": ToolSpec(
    name="web_search",
    label="网页搜索",
    description="按关键词搜索网页。",
    fields=(
        ToolField("SEARCH_API_KEY", "API Key", secret=True),
        ToolField("SEARCH_ENDPOINT", "接口地址", placeholder="https://…"),
        ToolField("SEARCH_MODE", "模式", kind="select",
                  options=(("fast", "快速"), ("deep", "深度")), default="fast"),
        ToolField("SEARCH_LIMIT", "结果数", kind="number", default="5"),
    ),
    tool_names=("web_search",),
    builder=_build_web_search,   # (config, ctx) -> list[BaseTool]
),
```

设置面板会自动出现这张卡片和对应控件；保存后下一轮对话里，`builder` 拿到的
`config` 就是这些值（缺省回落到 `.env` 同名变量与 `ToolField.default`）。

字段形态：`text`（默认，`multiline=True` 变多行）、`number`（保存时校验数字）、
`select`（必须给 `options`）、`switch`（存 `"true"` / `"false"`）。
`secret=True` 不回显、留空保持原值、可用「清除」真正清空；`required=True` 只在界面
标星，是否允许为空由工具自己判断。设 `risk=RISK_WRITE/EXEC` 会自动进入审批流程。

内置的 `mock_data` 就是这条链路的样板：在设置 → 工具里启用「模拟数据」并填一段文字，
agent 调用 `mock_lookup` 就会原样拿到它（未填写时返回 `Error: [missing_config]`）。

## 工具错误契约

所有工具失败时都**返回**以 `Error:` 开头的字符串，而不是抛异常（抛异常会中断整轮对话）：

```
Error: [read_required] 写入前必须先读取 a.txt 的现有内容。
请先调用 read_file 查看原文，再决定是整文件覆盖（write_file）还是局部替换（edit_file）。
```

`[code]` 是稳定的机读错误码，例如 `not_found`、`path_escape`、`read_required`、
`stale_read`、`ambiguous_match`、`bad_pattern`、`timeout`、`auth_config`。
统一前缀的意义在于模型靠它判断“这次调用失败了”：此前天气返回 `ERROR:`、时间返回
`Error:`，大小写不一致会漏判，然后在错误的前提上继续推理。

命令的非零退出码**不算**工具错误——命令确实执行了，只是结果失败，因此返回
`退出码：N（失败）` 加上 stderr，由模型决定下一步。

## 工具审批

`write`（改文件）与 `exec`（执行命令）两类工具在执行前需要人确认，`read` 类直接执行。
每次确认提供三种选择：

| 选择 | 作用范围 |
| --- | --- |
| 允许本次 | 只放行这一次调用 |
| 始终允许 | 本会话内记住「工具 + 对象」（命令原文 / 文件路径），之后同类调用自动放行 |
| 拒绝 | 工具不执行，返回 `Error: [approval_denied]`，模型应换方案而不是重试 |

- **网页端**：对话流里出现确认卡片，显示工具名、摘要与内容预览（命令原文、
  写入内容前 800 字符、`edit_file` 的 `-旧 / +新`），点按钮即回执。
  会话级授权可在「设置 → 工具 → 清除本会话『始终允许』」撤销。
- **命令行**：终端提示 `[y] 允许本次 / [a] 始终允许 / [n] 拒绝`；
  `/approvals` 查看已记住的授权，`/approvals clear` 清除，切换会话自动作废。
- **超时与断流**：等待超过 `APPROVAL_TIMEOUT` 秒按拒绝处理；客户端中途断开时，
  正在等待的审批立即按拒绝收尾，不把工具线程挂在后台。
- **失败关闭**：提问通道出错（异常、EOF、无人值守）一律视为拒绝，绝不默认放行。

拒绝只影响这一次调用，不会中断对话：模型收到 `Error: [approval_denied]` 后可以解释
原因、改用只读方式确认现状，或等你放行。自动化场景可用 `APPROVAL_ENABLED=0` 整体关闭。

> 实现上没有用 LangChain 自带的 `HumanInTheLoopMiddleware`（它基于 LangGraph 的
> `interrupt()`，需要 checkpointer 并把一轮对话拆成「暂停 + resume」），改用
> `wrap_tool_call` 拦截 + 同步等待回执，与「一次 SSE 流跑完一轮」的结构保持一致。

## 桌面外壳与打包（暂停）

本次重构把界面迁到 Vue，并改为前后端分离。Electron 外壳和 PyInstaller 打包**尚未适配**：

- `desktop/main.js` 仍然加载后端 `/`，现在只会看到 API JSON 而不是界面；
- `packaging/build.ps1` 仍可构建只含 `/api` 的后端 exe，但不再包含网页资源；
- 恢复桌面版时，需要让外壳加载 `frontend/dist`（开发时则启动 Vite），并重新设计
  sidecar 的静态资源路径。

浏览器使用不受影响，见上文「网页对话工作区」。已经踩过的桌面环境坑（受限终端
spawn EPERM、Chromium 沙箱、Electron 镜像等）保留在 [desktop/README.md](desktop/README.md)
和 [HANDOVER.md](HANDOVER.md)，后续恢复时再用。

## 命令行运行

```powershell
.\.venv\Scripts\python.exe main.py "北京现在几点？"        # 单轮提问（接着当前会话聊）
.\.venv\Scripts\python.exe main.py                        # 交互模式（多轮上下文）
.\.venv\Scripts\python.exe main.py --debug "东京现在几点？" # 额外打印本轮消息轨迹
.\.venv\Scripts\python.exe main.py --session work "..."    # 指定会话，历史跨进程复用
.\.venv\Scripts\python.exe main.py --new                   # 清空当前会话后再开始
.\.venv\Scripts\python.exe main.py --list-sessions         # 列出已保存的会话
```

命令行默认工作区是当前目录（可用 `WORKSPACE_DIR` 指定），文件类工具直接读写你
所在目录；`run_command` 同样默认启用，每次执行前会在终端询问。

### 交互命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示全部命令 |
| `/new [会话ID]` | 开启新会话（清空历史，不填 ID 则按时间生成） |
| `/session <会话ID>` | 切换到已有会话，继续之前的历史 |
| `/sessions` | 列出已保存的会话 |
| `/history [n]` | 查看当前会话最近 n 条消息 |
| `/approvals` | 查看本会话的「始终允许」；加 `clear` 全部清除 |
| `/compact` | 立即压缩上下文（把较早对话生成摘要） |
| `/status` | 查看轮次、消息数、token 估算与压缩阈值 |
| `/debug` | 切换调试模式（打印消息轨迹） |
| `/exit` | 退出 |

## 上下文机制

- **多轮记忆**：每轮把完整历史交给 Agent，因此追问、指代（“它”“刚才那个”）都能延续。
- **token 统计**：优先使用模型 API 返回的 `usage`（包含系统提示词、工具 schema 与回复，
  与计费口径一致），拿不到时才退回本地字符估算；`/status` 会标明数据来源。
- **压缩边界**：默认按模型窗口推导——`131072 × 80% − 8000 预留 ≈ 96857 tokens`，
  或历史超过 40 条消息时触发；两次自动压缩之间至少间隔 2 轮，避免“压完立刻再压”。
- **压缩方式**：较早对话压缩成中文摘要（以【历史对话摘要】开头）并保留最近 8 条消息；
  摘要失败时降级为“省略更早消息 + 明确提示”，对话不中断。
- **工具输出预算**：单条工具结果超过 8000 tokens 会先截断再写入历史并留下标记
  （类似 Codex 的 `tool_output_token_limit`），避免一次大输出撑爆窗口。
- **本地持久化**：会话历史保存在 `memory/<会话ID>.json`（可用 `MEMORY_DIR` 改目录），
  进程重启或换终端后仍可继续；文件里包含消息类型、工具调用参数与结果。
- **运行保护**：模型调用失败自动重试；单轮模型调用数与工具调用数都有上限，
  防止 Agent 陷入死循环。
- **命令手动控制**：`/compact` 立即压缩、`/status` 查看占用与阈值、`/new` 开新会话。

### 可调环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODEL_NAME` | `deepseek-v4-flash` | 对话模型名称 |
| `SESSION_ID` | `default` | 默认会话 ID |
| `MEMORY_DIR` | `<home>/memory` | 会话历史目录（覆盖用户级默认） |
| `MODEL_CONTEXT_WINDOW` | `131072` | 模型上下文窗口（token） |
| `COMPACT_TRIGGER_RATIO` | `0.8` | 触发压缩的窗口占比 |
| `COMPACT_RESERVE_TOKENS` | `8000` | 为回复与摘要请求预留的 token |
| `MAX_CONTEXT_TOKENS` | `0` | 绝对阈值覆盖；`0` 表示按窗口比例推导 |
| `MAX_CONTEXT_MESSAGES` | `40` | 触发压缩的消息条数阈值 |
| `KEEP_RECENT_MESSAGES` | `8` | 压缩后保留的最近消息数 |
| `MAX_TOOL_OUTPUT_TOKENS` | `8000` | 单条工具输出写入历史的预算 |
| `MAX_TOOL_OUTPUT_CHARS` | `20000` | 单个工具返回的字符上限（超出截断并标注） |
| `APPROVAL_ENABLED` | `1` | 设为 `0` 关闭工具审批（自动化场景） |
| `APPROVAL_TIMEOUT` | `300` | 等待人确认的秒数，超时按拒绝处理 |
| `COMMAND_TIMEOUT` | `60` | `run_command` 默认超时（秒） |
| `WORKSPACE_DIR` | 当前目录 | 命令行模式的默认工作区 |
| `MAX_MODEL_CALLS` | `8` | 单轮模型调用上限 |
| `MAX_TOOL_CALLS` | `15` | 单轮工具调用上限 |
| `MODEL_MAX_RETRIES` | `2` | 模型调用失败重试次数 |
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `8000` | 后端监听地址 |
| `FRONTEND_URL` | `http://127.0.0.1:5173` | 前端开发地址（日志与 `/` 返回里展示） |
| `CORS_ORIGINS` | 本地 5173 / 4173 | 允许跨域的前端来源，逗号分隔 |
| `SMART_AGENT_TOKEN` | 未设置 | 设置后 API 需要令牌（桌面外壳自动注入） |
| `SMART_AGENT_PORT` | 未设置 | 覆盖监听端口，`0` 由系统分配 |
| `DATA_DIR` | `<home>/data` | 网页配置目录（覆盖用户级默认） |
| `SMART_AGENT_HOME` | 平台用户目录 | 整个数据根目录，见「数据目录」 |

前端自己的变量写在 `frontend/.env.local`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000` | Vite 开发/预览服务器把 `/api` 代理到哪里 |
| `VITE_API_BASE_URL` | 空 | 直连后端地址；留空表示请求同源 `/api` |

## 时间工具

`get_current_time(timezone_name="local")` 支持四类输入（大小写、空格不敏感）：

| 写法 | 示例 |
| --- | --- |
| 本地时间 | `local`、`本地`、`本机`、`系统时间` |
| IANA 时区 | `Asia/Shanghai`、`UTC`、`America/New_York`、`asia/shanghai` |
| 城市 / 省份 / 国家 | `北京`、`上海`、`纽约`、`东京`、`日本`、`美国西部` |
| UTC 偏移 | `UTC+8`、`GMT-5`、`+08:00`、`东八区`、`西五区` |

返回示例：

```
地区：北京 → Asia/Shanghai
时间：2026-11-04 09:21:11（星期三）
时区：CST，UTC+08:00，夏令时：否
ISO 8601：2026-11-04T09:21:11+08:00
Unix 时间戳：1793755271
```

输入无法识别时返回以 `Error:` 开头的说明，并附带相近候选，方便 Agent
自我纠正；`list_timezones(keyword)` 可以列出支持的地区与 IANA 名称。

### 时区数据（tzdata）

Windows 默认不带 IANA 时区数据库，Python 的 `zoneinfo` 需要 `tzdata`
包（已写入 `pyproject.toml`，`uv sync` 会自动安装）。如果运行环境缺少
`tzdata`，工具会退回内置常用时区偏移表，并在结果中以“注意：”标注
“未应用夏令时”——此时有夏令时的地区（如伦敦、纽约）可能相差 1 小时。

## 测试

```powershell
# 后端：201 个离线测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py" -v

# 前端：类型检查 + 生产构建
cd frontend
npm run build
```

测试覆盖时间工具（别名 / 偏移 / 夏令时 / 兜底）、会话持久化、上下文压缩、
失败回滚、命令行交互命令，以及网页端接口（模型 CRUD、工具开关、密钥掩码、
工作区浏览、SSE 流式对话、CORS 预检），工具注册表与错误契约，文件工具（含路径逃逸、
读后写、stale 检测、截断）与命令工具（退出码、超时、截断），工具审批
（允许本次 / 始终允许 / 拒绝、失败关闭、超时与断流、SSE 回执闭环）。
全部离线运行（不调用真实模型）。
