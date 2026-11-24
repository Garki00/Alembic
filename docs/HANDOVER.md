# ALEMBIC 交接文档（HANDOVER）

> 给接手的智能体 / 开发者：先读这一份，再动代码。
> 最后更新：2026-11-21，恢复 Electron 桌面版（本地静态前端 + `/api` 代理），安装器改为
> 原生 NSIS，主程序和安装器默认管理员；安装时自动创建命令沙盒、校验内置运行时。
> 发布流程见 [packaging.md](packaging.md)。此前一轮完成 Windows 原生命令沙盒、
> 后台任务、附件与上下文压缩，467 个后端测试全绿（1 个跳过）。

## 1. 这是什么

一个**本地优先的智能体工作区**：

- 后端：Python 3.11 + FastAPI + LangChain 1.4.8，默认走 `create_agent` 的
  **模型原生工具调用**（结构化 `tool_calls`，观测只能由运行时产生）；设置
  `AGENT_ENGINE=react` 可切到 `agents/react.py` 的文本 ReAct 循环（已硬化）。
  模型供应商支持深度求索 / OpenAI / Claude / 千问 / 硅基流动 / 自定义，统一走
  OpenAI 兼容接口（Claude 走 Anthropic 兼容端点）；一个供应商下可挂多个模型，
  每个模型有独立的可选推理等级集合（none/low/high/max）、当前等级、温度、
  是否支持扩展思考/图片输入、上下文长度与最大输出 tokens；默认上限由
  `default_model_limits()` 按模型家族给出；
- 命令执行实时推送 stdout/stderr；停止生成会通过 `cancel_event` 结束前台命令，
  并停止当前会话仍在运行的后台任务；Windows 用 Job Object 按进程树停止，
  其他平台没有原生沙盒后端时命令 fail closed；
- **Windows 命令沙盒**：`scripts/setup-sandbox.ps1` 创建两个专用本地用户；
  `tools/sandbox/windows.py` 用 `CreateProcessWithLogonW` 启动，ACL 限定工作区，
  `tools/winjob.py` 管理进程树，防火墙按用户阻断网络；`allowlist` 模式只允许本机
  HTTP CONNECT 代理，域名由 `tools/sandbox/proxy.py` 校验。没有沙盒时命令失败关闭，
  没有宿主机回退；
- 后台任务有独立进程视图：`GET /api/processes` 提供运行中与已完成列表，
  `/api/processes/events` 推送完成事件；`start_process(notify=True)` 的任务退出后
  自动触发一次内部续跑，长期服务可设 `notify=False`；
- 输入区支持附件上传（写入工作区 `.alembic/attachments/`）、手动压缩上下文，
  并用圆环显示上下文占用百分比；
- 首次启动且没有可用供应商时，前端显示**初始化向导**：选择带图标的供应商模板、
  维护模型列表、填写 Base URL / API Key，再设置工作步骤展示与收起时机；
- 前端：Vue 3.5 + Vite 7 + TypeScript + Pinia（`frontend/`）；
- 两者通过 `/api` 通信：开发时前端跑 5173，后端跑 8000，Vite 代理 `/api`；
- 后端**不再提供 HTML / 静态资源**，只提供 JSON 与 SSE；
- Electron 外壳（`desktop/`）在 sidecar 之外再起一个本地静态服务器，提供
  `frontend/dist` 并把 `/api` 反向代理到后端；PyInstaller 后端作为 sidecar 随包分发；
- 安装器是**原生 NSIS**（`packaging/nsis/`），带与前端一致的纸色 / 祖母绿页面；
  postinstall 在 `packaging/install/nsis-postinstall.ps1`，发布流程见
  [packaging.md](packaging.md)。主程序和安装器都默认请求管理员权限。

数据（模型配置、工具开关、工作区、会话历史）全部存放在用户目录，
`%APPDATA%\Alembic\{data,memory}`，不在代码目录里。旧的 `%APPDATA%\SmartAgent`
会在首次启动时由 `config/storage.py` 复制到新目录，原目录保留。

> 注意区分两个目录：`%APPDATA%\Alembic` 是**业务数据**（Python 后端写）；
> `%LOCALAPPDATA%\Alembic` 是 **Electron 自己的 profile**（缓存、`runtime/*.log`）。
> 安装版的卸载器可以选择同时删除这两处。

## 2. 30 秒上手

```powershell
uv sync                                              # 建 .venv、装 Python 依赖
cd frontend; npm install; cd ..                      # 装前端依赖

# 管理员 PowerShell，仅一次
.\scripts\setup-sandbox.ps1

.\scripts\dev.ps1                                    # 后端 8000 + 前端 5173，自动开浏览器

# 或者两个终端：
.\.venv\Scripts\python.exe -m web                    # http://127.0.0.1:8000/api
cd frontend; npm run dev                             # http://127.0.0.1:5173
```

验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"   # 467 个测试
cd frontend
npm run build                                        # vue-tsc + vite build
```

命令行用法不变，但不再有默认模型名：`.env` 里需要同时配置 `DEEPSEEK_API_KEY`
和 `MODEL_NAME`，再运行 `.\\.venv\\Scripts\\python.exe main.py "北京现在几点？"`。

## 3. 目录与职责

```
main.py                  CLI 入口（argparse + ChatCLI，含 /approvals 等命令）
run_backend.py           PyInstaller 入口，等价于 python -m web
agents/
  agent.py               CLI 侧装配：model / tools / engine 选择（react 或 tool_calling）
  tool_visibility.py     扩展工具可见性中间件：按会话激活状态过滤模型 schema
  react.py               可选文本 ReAct 循环：stop 序列、严格单步解析、审批、限流、流式事件
  context.py             ChatSession、SessionStore、上下文压缩、SessionMeta
  approval.py            审批策略、中间件、终端提问器
config/
  settings.py            环境变量 + app_home 解析 + 前端地址 / CORS 来源 + AGENT_ENGINE
  storage.py             ensure_storage：首次运行把仓库 data/ memory/ 迁到 app_home
  store.py               ModelStore / ToolStore / WorkspaceStore / PreferencesStore + 迁移
prompts/system.py        分层系统提示词：工具规则 / 执行纪律 / 完成标准 / 运行时 / 界面
prompts/context.py       ALEMBIC.md / AGENTS.md / CLAUDE.md / .cursorrules 加载、扫描、截断
prompts/react.py         英文 ReAct 格式与工具目录模板
tools/
  registry.py            ★ 工具清单唯一事实来源：TOOL_SPECS / ToolField / build_tools
  catalog.py             ★ 基础/扩展工具目录、ToolActivation、tools_search 搜索与激活
  context.py             ToolContext（workspace、输出预算、终端偏好、扩展工具激活状态）
  errors.py              统一错误契约 tool_error() / truncate()
  paths.py               工作区路径解析、逃逸防护、凭据路径保护
  command_policy.py      命令策略：hardline 直接拒绝 / elevated 风险原因
  command_brief.py       命令 → 人话说明（离线拆语句/管道，供审批卡片使用）
  env.py                 子进程环境变量白名单（剥离模型密钥）
  terminals.py          终端偏好与 PowerShell / CMD / Git Bash / WSL 构造
  winjob.py              Windows Job Object：按进程树停止命令与后台任务
  sandbox/
    base.py             沙盒策略 / 状态 / 进程协议；没有可用后端时 fail closed
    windows.py          专用本地用户、ACL、CreateProcessWithLogonW、Job Object
    proxy.py            本机 HTTP CONNECT 代理与域名允许列表
    config.py           data/sandbox.json 网络模式与允许域名
  files.py               list_dir / read_file / search_files / find_files / file_stat
                         write_file / edit_file / make_dir / delete_file / move_file / copy_file
  documents.py           read_document（PDF / DOCX / XLSX / PPTX / 文本，按字符偏移分段）
  images.py              read_image（把图片交给当前视觉模型，返回文字描述）
  fetch.py               fetch_url（SSRF 防护、重定向校验、HTML 转文本）
  search.py              web_search（Bing / DuckDuckGo / Tavily / Brave / Serper / SearXNG）
  download.py            download_file（流式下载、SSRF、大小上限、.part 保护）
  patch.py               apply_patch（标准 unified diff，多文件 / 多 hunk）
  atomic_edit.py         batch_edit（多处精确替换，校验后原子提交/回滚）
  git.py                 git_status / git_diff / git_commit（参数列表调用，不走 shell）
  todo.py                todo_write + TodoStore（会话级任务清单）
  questions.py           ask_user_question / Question / 终端提问通道
  processes.py           start_process / list_processes / read_process_logs / wait_process / stop_process
                         状态、时长、完成回调与 Job Object
  shell.py               run_command（实时输出、取消、进程树、超时、环境白名单）
  time.py weather.py mock.py
web/
  __main__.py            自己 bind socket（支持 port=0）、写端口文件、ensure_storage、跑 uvicorn
  server.py              只提供 /api：路由、SSE 对话、审批、工作区 / 会话元数据、供应商、进程视图
  runtime.py             build_runtime：按当前设置装配 agent
  process_events.py      后台任务事件 hub：启动 / 完成事件分发给 SSE 客户端
  auth.py                可选 token 中间件（Bearer / Cookie；放行 OPTIONS 与 /api/health）
  approvals.py           ApprovalRegistry / ApprovalBroker（SSE ↔ HTTP 回执）
  questions.py           QuestionRegistry / QuestionBroker（ask_user_question 的 SSE ↔ HTTP 回执）
frontend/
  package.json           Vue / Vite / Pinia / TypeScript
  vite.config.ts         /api 代理、5173 / 4173 端口
  index.html
  src/
    main.ts              挂载 + 读取 ?token=
    App.vue              外壳、初始化、深链接、全局 Esc
    api/
      client.ts          fetch 封装、鉴权头、错误信息
      chat.ts            POST /api/chat 的 SSE 解析
      types.ts           后端 snake_case 数据结构类型
    components/          侧边栏（AppSidebar / WorkspaceGroup / SessionRow）、对话流、审批卡、问答卡、任务清单、工作步骤、代码工作视图、后台任务面板、通用设置、工具网格/列表、输入区、设置、初始化向导、供应商编辑器/图标/选择器、GlobalTooltip、FloatingMenu、ConfirmDialog、PanelToggle、Toast
    lib/
      markdown.ts        轻量 Markdown 渲染（从旧 app.js 迁移，无第三方依赖）
      providers.ts       ★ 供应商模板唯一事实来源：名称 / Base URL / 图标
      preferences.ts     工作步骤展示与收起时机的选项（通用设置 + 初始化向导共用）
      time.ts            对话列表的相对时间（刚刚 / 分钟 / 小时 / 天）
    stores/
      workspace.ts       Pinia：模型 / 工具 / 工作区 / 会话列表 / 通用设置与 CRUD
      chat.ts            当前会话、流式消息、审批/提问回执、任务清单、附件、压缩、续跑
      processes.ts       后台任务列表、详情轮询与完成事件
      ui.ts              弹窗、搜索、折叠状态、对话分组/排序、Toast、推荐问题草稿
    assets/main.css      设计系统（纸白 + 薄荷绿）
scripts/
  dev.ps1 / dev.cmd      一键启动本地开发环境
  setup-sandbox.ps1      管理员一次性初始化 Windows 命令沙盒
  verify-sandbox.ps1     校验沙盒用户、防火墙与配置
  reset-config.ps1/.cmd  清除 Alembic / SmartAgent 配置，重新查看初始化向导
desktop/                 Electron 外壳（静态前端 + /api 代理 + 卸载界面）
packaging/               PyInstaller、发布编排、NSIS 页面 / 位图与安装/卸载脚本
tests/                   *_test.py，全部离线（不调真实模型）
```

## 4. 关键数据流

### 4.1 配置 → 工具

```
data/tools.json → ToolStore.configs() → build_runtime(..., tool_configs=)
→ build_tools(enabled, configs, ctx) → spec.builder(config, ctx) → LangChain tools
```

- 条目的启用状态与参数都在 `data/tools.json`，结构版本写在 `version` 字段（当前 4）。旧 `file_read`、`file_write`、`background_process`、`process_inspect`、`git_read` 组迁移为 18 个独立条目，保留启用状态与配置；显式成员设置优先。`LEGACY_TOOL_GROUPS` / `expand_enabled_names` 保留旧入口兼容。
- `extensions/manager.py` 发现原生工具、MCP 和 Skills，并发布到同一工具注册表；扩展配置保存在 `data/extensions.json`。默认目录为数据目录父级的 `extensions/{tools,mcp,skills}`，启动自动创建。
- 原生插件默认禁用，扫描不导入代码；实际调用属于可信宿主代码。MCP 默认禁用，启用后通过 SDK 连接。两类操作都沿用 `exec` 风险与现有审批，不能视为受限命令沙盒。
- Skills 以元数据目录、`load_skill`、`read_skill_resource` 渐进读取；`allowed-tools` 不授予权限。刷新用于后续构建的 Agent，不替换运行中一轮的 schema。协议范围、配置 API、安装示例及 OAuth 限制见 [extensions.md](extensions.md)。
- `ToolStore.public()` 把 `ToolField` 序列化给前端：`kind/options/required/secret/multiline`…
- 密钥字段**永远不回显**：`value=""` + `value_set` + `hint`（`••••1234` / `12 字符`）。
- 保存时空字符串表示「保持原值」；要清空必须走 `PUT /api/tools/{name}` 的 `clear: [key]`。

### 4.2 Agent 一轮

默认 `tool_calling`（`create_agent`）：模型返回结构化 `tool_calls`，运行时逐个执行并把
真实的 `ToolMessage` 交回；审批由 `ApprovalMiddleware` 处理。观测只能由运行时产生，
不存在「文本解析把动作吞掉」这一类故障。

```
ChatSession.ask_stream
  → agent.stream({"messages": history}, stream_mode=["messages","values"])
     → 模型返回 tool_calls → ApprovalMiddleware → tool.invoke(args)
     → 工具结果作为 ToolMessage 回到模型 → 直到没有 tool_calls
  → 带 tool_calls 的 AI 正文派生 thought 事件（工作步骤）
```

可选 `AGENT_ENGINE=react`：`ReActAgent._run` 走文本 ReAct，且做了协议硬化：

```
  → build_react_prompt(history, question, scratchpad)   # 英文模板
  → model.invoke(..., stop=["\nObservation:", ...])
  → 模型自写 Observation / Action+Final 混排 / 多 Action → 判协议错误，回喂纠错后重试
  → 有 Final Answer → 追加 AIMessage(final)，结束
  → 有 Action/Action Input → ApprovalGate → tool.invoke(args)
       → ToolMessage(observation) → scratchpad → 下一轮模型调用
```

- `ReActAgent` 同时实现 `invoke` 与 `stream`，因此 CLI、SSE、上下文压缩、历史持久化
  不需要知道底层引擎。
- ReAct 的中间 `Thought` 通过独立 `thought` 事件推送；`tool_calling` 没有文本 Thought，
  由「带 `tool_calls` 的 AI 正文」派生同一个事件（`ChatSession.ask_stream` 里
  `derive_step_thoughts`），两条路径的工作步骤视图一致。
- ReAct 的 `stop` 序列让模型写完 `Action Input` 就停，无法继续编造 Observation；
  提示词与解析器都要求「一条消息 = 一个 Action 或一个 Final Answer」。

### 4.3 对话 → SSE

```
Vue fetch POST /api/chat
  → Vite dev proxy → FastAPI
  → 服务端起工作线程跑 ChatSession.ask_stream，事件推到 queue.Queue
  → 主线程从队列取出后写 SSE
  → frontend/src/api/chat.ts 按 "\n\n" 切帧 → chat store 更新当前 assistant turn
```

事件：`meta` / `delta` / `thought` / `tool` / `tool_output` / `tool_result` / `approval` /
`approval_expired` / `question` / `question_expired` / `todo` / `compacted` / `done` / `error`。

`tool_output` 由 `tools/shell.py` 的读取线程通过 `ctx.emit_event` 发出，前端把它追加到当前
运行中的工具事件；最终 `tool_result` 到达后清空实时输出，避免重复。后台任务使用独立的
`GET /api/processes/events` SSE：`process_started` / `process_completed` 由
`web/process_events.py` 分发，进程视图和自动续跑共用这条流。

停止生成时前端先 `POST /api/sessions/{id}/cancel`，再断开 `/api/chat` 的 SSE；
服务端把该会话的 `threading.Event` 置位，`run_command` 轮询后结束进程树；同一个
接口默认还会调用 `ProcessManager.stop_session(session_id)`，停止当前会话所有运行中的
后台任务。切换会话只断开旧流时使用 `?background=0`，不误杀旧会话的后台任务。
即使取消请求失败，`event_stream` 的 `finally` 也会置位，保证前台命令不会继续跑。

> 后台任务完成后的续跑由前端发起：`processes.ts` 收到 `process_completed` 且
> `notify=true` 时调用 `chat.queueProcessContinuation`，等当前轮结束后再以
> `internal=true` 调 `/api/chat`；服务端用 `process_id` 去重，内部消息在历史里标记
> `internal`，前端重新加载时不会把它渲染成用户输入。


> **不要改回「生成器里直接跑 agent」**。审批需要工具线程阻塞等待浏览器回执，
> 生成器一旦阻塞就发不出 `approval` 事件。

> 前端流式渲染用 `requestAnimationFrame` 合并高频 `delta`，不要每帧重写整篇 Markdown。

### 4.4 审批

```
ReActAgent._gate_tool（tool_calling 模式则是 ApprovalMiddleware.wrap_tool_call）
  → ApprovalPolicy.needs_approval / is_allowed（工具名 + 目标对象）
  → ApprovalGate.ask(request)
      网页：ApprovalBroker.ask → 推 SSE → 阻塞在 threading.Event
            → 前端 ApprovalCard → POST /api/approvals/{id}
      终端：console_asker → input()
  → 允许执行 / 返回 ToolMessage(status="error", "Error: [approval_denied] …")
```

- 只有 `risk` 为 `write` / `exec` 的工具会被拦。
- 「始终允许」的键：`file:<路径>`（写文件两种工具共用）或 `run_command:<命令原文>`。
- 失败关闭：提问通道异常、超时、断流、EOF 一律按拒绝。
- 前端审批按钮在等待回执时禁用；回执成功后卡片不再显示按钮。

**审批卡片先讲人话，再给原文**：

- `summary` 是「这条请求要做什么」，不是命令原文：
  - `run_command` / `start_process` 优先用模型自填的 `description` 参数
    （系统提示词要求每次必填，例如「扫描当前目录的 .html 文件并检查本地链接」）；
  - 模型没填时由 `tools/command_brief.py` 离线翻译命令结构（`Get-ChildItem`、
    `Select-String`、`git`、`npm` 等）；`--- xxx ---` 分段标题会变成小标题，
    `foreach` 循环与正则计数也会被翻成中文；
  - 规则完全看不懂时退化为「执行一条 <程序> 命令，原文见下方」，绝不把整段命令
    塞进说明行。
- `steps` 是命令拆出的分步说明（仅命令类工具、且多于一步时随 SSE 下发），
  卡片用有序列表展示；`preview` 仍是命令/文件原文，默认展开、可折叠，命令原文
  不再按 800 字符截断（上限 8000，卡片内滚动）。
- 前端 `ApprovalCard.vue`：`run_command` / `start_process` / `write_file` /
  `edit_file` / `apply_patch` 的原文默认展开，其余工具的详细内容默认收起。

### 4.5 前端状态

```
App.vue onMounted
  → GET /api/bootstrap → workspace store（providers/tools/workspaces/sessions/preferences/needs_setup）
  → needs_setup=true → 渲染 OnboardingModal（保存供应商与偏好后关闭）
  → 没有工作区 → WelcomePanel 显示「选择工作区」，composer 隐藏
  → 有工作区 → 打开第一个会话 → GET /api/sessions/{id}/messages
  → history 消息按「user 一轮 / assistant+tool 聚合」映射为 ChatTurn[]
  → 后续 POST /api/chat 的 SSE 事件直接改当前 assistant turn
```

- 数据单位是**供应商**：`providers[].models[]`，每个模型有 `thinking_levels`
  （可多选）与当前 `thinking`（none/low/high/max）、temperature、supports_reasoning、
  supports_images、context_length、max_output_tokens；`active_provider_id` +
  `active_model_id` 指向当前模型。默认值由 `default_model_limits()` 按模型家族给出，
  用户可在供应商编辑的每行左侧箭头里覆盖。
- 新安装**不自动创建工作区**：`WorkspaceStore._seed()` 返回空列表；前端
  `App.initialize()` 在没有工作区时直接显示 `WelcomePanel` 的「选择工作区」按钮，
  并隐藏输入框。添加工作区后才出现 composer。
- `needs_setup` 由后端 `ModelStore.needs_setup()` 判定：没有「API Key + Base URL +
  至少一个模型」齐全的供应商时为 true；供应商增删改后前端会重算并自动弹出向导。
- 供应商模板只在前端 `lib/providers.ts` 维护（名称、Base URL、图标）；设置 → 供应商
  与初始化向导共用 `ProviderEditor`，新增供应商只改这一处。
- 前端保存供应商走 `POST /api/providers`（模型列表随供应商整包提交）；
  激活模型走 `POST /api/providers/{id}/activate`；从上游获取走
  `POST /api/providers/fetch`（只返回候选模型 ID，不写库）；
  调整当前推理等级走 `PATCH /api/providers/{id}/models/{model_id}/thinking`。
- `POST /api/chat` 的 `session_id` 为空/null 时由后端生成新会话 ID：在工作区里
  直接输入发送即等于新建对话，不再要求先点左侧「＋」。
- 运行时在 `web/runtime.py` 里按 provider 决定 `ChatDeepSeek` / `ChatOpenAI`，
  当前 `thinking != none` 时映射为 `reasoning_effort`。
- 旧的扁平 `models` 列表会在 `ModelStore._load()` 里自动迁移成供应商 + 模型列表。

- 会话切换时 `chat.ts` 会递增内部 `activeStream`，旧流的回调不会污染新会话。
- 左栏搜索同时匹配工作区名与对话标题；工作区名命中时展示该工作区全部对话。
- 旧的会话没有 `workspace_id` 时，前端按「归到第一个工作区」显示（与后端兼容策略一致）。
- 通用设置保存在 `data/preferences.json`，后端通过 `GET/PUT /api/preferences` 读写；
  前端 `workspace.preferences` 是唯一数据源，`WorkSteps` / `CodeWorkView` 只读它。
- `permission_mode` 决定审批范围：`read_only` 真正只读（写/执行在审批前拒绝）、
  `approval_all` 全部工具审批、`workspace_write` 只审批执行命令、`full_access`
  不审批（不可恢复的高危命令仍由命令策略拒绝）；服务端在每轮聊天开始时按它
  构造 `ApprovalGate`。旧配置里的 `read_only` 会在读取时迁移为 `approval_all`。
- 命令策略在 `tools/command_policy.py`：`hardline` 直接拒绝，`elevated` 把风险
  原因带进审批卡片。文件工具在 `tools/paths.py` 里额外拒绝 `.ssh`、`.aws`、
  私钥与 `.env` 读取；子进程环境变量由 `tools/env.py` 白名单过滤，密钥不继承。
- 终端偏好保存在 `preferences.terminal`（powershell / cmd / gitbash / wsl），
  由 `tools/terminals.py` 适配；`run_command` / `start_process` 都走同一套构造。
- AI 回复里的网页链接按 `preferences.link_target` 打开：默认在右侧边栏新标签页
  （`inspector.openLink`），也可以交给系统浏览器。
- 审批 / 提问卡渲染在 `ChatComposer`，待处理时替换输入栏；助手消息只保留已处理结果。
- 右侧边栏 `InspectorSidebar` 通过 `/api/workspace/files` 与 `/api/workspace/file`
  浏览工作区；标签页最多 3 个。文件树、预览、浏览器都是独立组件。
- 文件工具的返回值带 `变更：+N -M`，`TurnSummary` 据此在完成轮次后汇总修改文件。
- `AssistantTurn.workEvents` 按发生顺序保存 `thought` 与 `tool` 事件；
  `tool_result` 按 `tool_call_id` 回填到对应工具事件，而不是单独堆在末尾。
- 工具执行期间 `tool_output` 追加到 `WorkEvent.liveOutput`，`WorkSteps` 在工具行展开时
  实时显示；最终 `tool_result` 到达后清空 live 输出。
- 输入区附件先上传到 `<workspace>/.alembic/attachments/<会话ID>/`，再以路径列表随
  `POST /api/chat` 发送；服务端构造 `[Attached files]` 块，前端历史映射时再拆回附件 chip。
- `contextTokens/contextMax` 由会话历史和 `done` 事件的 `max_tokens` 更新；
  `ChatComposer` 的圆环只读 `contextPercent`，手动压缩接口返回新值。
- 后台任务面板 `ProcessPanel` 通过 `stores/processes.ts` 轮询列表和详情；
  `App.vue` 启动时建立 `/api/processes/events` 长连接，完成事件用于计数和自动续跑。
- `tool_result` 事件让工作步骤在流式期间就能显示结果；历史会话通过
  `ToolMessage.tool_call_id` 与 `AIMessage.tool_calls[].id` 配对。
- 会话元数据 `SessionMeta` 现包含 `title` / `pinned` / `archived`；首轮完成后
  `ChatSession._finish_turn` 调用 `_generate_title`（使用摘要模型）生成短标题，
  失败或没有摘要模型时回退 `title_from_text` 的首条内容前 40 字；标题随会话落盘，
  `_session_title` 优先读 meta.title。对话列表的置顶 / 重命名 / 归档 / 分支通过
  `PATCH /api/sessions/{id}` 与 `POST /api/sessions/{id}/branch` 持久化；分支标题
  取 `原标题(n)`。
- 左栏工具栏固定三个图标按钮（视图选项 / 搜索 / 添加工作区）；视图状态在
  `stores/ui.ts`（`sessionGrouping` / `sessionSort` / `manualOrder` / `searchOpen`）。
  `manualOrder` 只存在内存，切回最近更新即清空；拖动在列表内显示插入线。
- 对话与工作区的「更多」菜单使用 `FloatingMenu`（Teleport 到 body、固定定位、
  优先在按钮右侧展开），不再受 `workspace-list` 滚动裁剪，也不改变列表布局。
- 删除对话 / 工作区统一使用 `ConfirmDialog`：居中确认、第一次点「删除」后需再点
  「再次点击删除」；成功删除不再弹顶部 Toast。
- `FileViewer` 支持 PNG / JPEG / GIF / WebP / SVG 图片预览（后端返回 data URL，
  单张上限 8 MB）；其他二进制仍提示不支持。
- 设置 → 扩展按「工具 / MCP / Skill」切换，按内置 / 插件分组；工具保留网格 / 列表切换与独立开关，支持刷新及安装目录编辑。

### 4.6 命令沙盒

```
run_command / start_process
  → ToolContext.sandbox.spawn(command, cwd, env, SandboxPolicy)
  → WindowsSandbox.prepare_workspace：icacls 给专用用户授工作区 Modify、加凭据 deny
  → CreateProcessWithLogonW(专用本地用户, 独立管道)
  → WindowsJob 加入进程树
  → 命令退出 → 进程结束 → 日志/状态
```

- `offline` 使用 `AlembicSandboxOffline`，防火墙阻断全部出站/入站。
- `allowlist` 使用 `AlembicSandboxOnline`，只允许到本机代理端口；代理再按域名放行。
- `scripts/setup-sandbox.ps1` 是唯一需要管理员权限的步骤；应用运行时不提权。
- 没有可用后端时 `run_command` / `start_process` 返回 `sandbox_unavailable`；不允许直接 `subprocess.Popen`。
- 文件工具不经过该子进程沙盒，仍由 `tools/paths.py` 做工作区与凭据校验。
- Windows 下 WSL 终端被拒绝，因为 WSL 服务不能保证继承沙盒用户令牌和 Job Object。

## 5. 不可破坏的约定

1. **工具清单只有一处**：`tools/registry.py`。不要在 `agents/`、`web/`、`frontend/` 里再写工具名字列表。
2. **工具失败返回字符串**，格式 `Error: [code] message`，可选第二行建议；不要抛异常
   （抛异常会中断整轮对话）。
3. **文件/命令工具必须经过** `tools/paths.resolve_in_workspace`；越界返回 `path_escape`。
4. **覆盖已有文件前必须读过**（`read_required`），读取后被外部改动要报 `stale_read`。
5. **密钥不回显**；空值=保持；清空用 `clear`。
6. **中间件顺序**：retry → model limit → tool limit → approval（审批在最内层）。
7. **会话归属工作区**：`SessionMeta.workspace_id`，前端按它分组；没有该字段的旧会话回落到第一个工作区。
8. **用户数据不写在代码目录**：打包后代码在只读位置，一切写入走 `settings.app_home`。
9. **后端只提供 `/api`**：不要在 `web/server.py` 重新挂 `StaticFiles` 或返回 `index.html`。
   页面是 `frontend/` 的职责；生产部署应由独立静态服务器 / 反向代理提供。
10. **CORS 与鉴权顺序**：先 `install_auth(app, token)`，再 `add_middleware(CORSMiddleware)`；
    `auth.py` 必须放行 `OPTIONS`（预检不带 Authorization）。否则跨域请求会被 401 挡住。
11. **模型输出必须经过 `lib/markdown.ts`** 转义后再 `v-html`；不要直接把模型文本塞进 `innerHTML`。
12. **前端字段保持后端 snake_case**（`workspace_id`、`api_key_set`、`base_url`…），
    不要在 API 层改名，避免两套映射。
13. **SSE 帧格式**是 `event: xxx\ndata: {json}\n\n`；前端解析器依赖这个空行分隔。
14. **桌面版若恢复**：Electron 里 `console.log` 必须 try/catch（打包后 EPIPE），
    `--no-sandbox` 只能来自命令行，PyInstaller 必须带 `tzdata`。
15. **文件写/删/移动都保护工作区根与 `.git`**；删除非空目录必须显式 `recursive=True`。
16. **提问失败关闭**：`ask_user_question` 没有通道、超时、流断开都返回 `Error:`，
    不要伪造用户回答；网页端回答走 `POST /api/questions/{id}`。
17. **工具事件走 `ctx.emit_event`**（如 `todo`），前端对未知事件应忽略；不要从工具里
    直接 import `web.*`，否则 CLI 会被网页依赖拖下水。
18. **后台进程只能操作 `ProcessManager` 自己启动的 ID**，不接受任意 PID；停止时按进程树处理。
19. **提示词统一用英文**：`prompts/system.py`、`prompts/react.py`、`agents/context.py`
    的摘要提示词；但要求模型用用户的语言回复。新增提示词不要写回中文。
    系统提示词按 `surface`（`web` / `cli`）只改变回复格式提示；工具规则必须根据
    当前实际可用的工具名生成，不能写死未启用的工具。项目上下文文件按
    `ALEMBIC.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules` 取第一个，
    经过注入扫描与 20k 截断后再注入，且不能覆盖安全/审批规则。
20. **默认引擎是 `tool_calling`**：`create_agent` 的结构化工具调用是主路径；
    `AGENT_ENGINE=react` 是可选兼容模式。改 `agents/react.py` 时必须保持
    `invoke` / `stream` 接口与现有 `ChatSession` 兼容，并保留 stop 序列与严格单步解析。
21. **ReAct 一条消息只能是单步**：解析器先拒绝模型自写 `Observation:`，再拒绝
    `Action` 与 `Final Answer` 混排和多 Action；不要改回「Final Answer 优先」。
    模型伪造的观测绝不能被当作工具结果写进历史。
22. **通用设置只有一处**：后端 `config/store.py: PreferencesStore`，前端
    `workspace.preferences`；不要在组件里另存 localStorage，否则刷新/换浏览器会分叉。
23. **工作步骤策略**：`compact / standard / detailed / verbose` 四档、`completion /
    next_input` 两种收起时机；`verbose` 不自动收起，手动展开的轮次不被强制收起。
24. **工具结果必须带 `tool_call_id`**：`tool` 与 `tool_result` 事件靠 id 配对；
    历史消息也通过 `AIMessage.tool_calls[].id` / `ToolMessage.tool_call_id` 保持配对。
25. **工作步骤按 `workEvents` 顺序渲染**：思考与工具调用交错，不能把所有思考
    单独堆在顶部；每行先给动作标签 + 一行摘要，详情按需展开。
26. **工具返回的事实才算事实**：`write_file` 落盘后会 stat 校验，失败返回
    `write_unverified`；不要在提示词或前端里把模型的「已保存」当作写入成功的证据。
27. **供应商模板只有一处**：前端 `lib/providers.ts`（名称 / Base URL / 图标）。
    初始化向导与设置 → 供应商都从它派生；后端存供应商对象（Base URL + Key + 模型列表），
    不要把供应商清单或 Base URL 再写进组件。
28. **首次初始化由 `needs_setup` 驱动**：没有「Key + Base URL + 至少一个模型」齐全的
    供应商就显示向导；不要自动生成默认模型（`MODEL_NAME` 默认空）。
29. **实时天气默认关闭**：`get_weather.default_enabled=False`，API Host 没有默认值；
    tools 配置 v3 迁移会把旧配置里的 `get_weather` 关闭一次。启用前必须填写
    用户自己的 QWeather 凭据与 API Host。
30. **模型属于供应商**：API/前端都以供应商为单位；模型列表、思考等级
    （none/low/high/max）、温度在供应商编辑里维护。不要再增加独立的「导入单个模型」入口。
31. **新安装没有默认工作区**：`WorkspaceStore._seed()` 必须保持空列表；前端在
    `workspaces` 为空时显示「选择工作区」按钮并隐藏 composer，不能把仓库目录
    自动当工作区。
32. **直接发送即创建对话**：`/api/chat` 收到空/null `session_id` 时必须生成新的
    `chat-*` 会话，而不是落到固定的 `default` 会话；工作区归属由请求里的
    `workspace_id` 决定。
33. **推理等级是可选集合 + 当前值**：`thinking_levels` 定义滑块吸附点，`thinking`
    是当前档位；默认集合为 low/high/max（不含 none），当前值默认 high。设置里
    多选集合，composer 的滑条只改当前值，二者都走供应商 API。
34. **停止生成必须取消进程**：前端 `POST /api/sessions/{id}/cancel`，服务端把
    `threading.Event` 传给 `ToolContext.cancel_event`；`run_command` 轮询它并结束
    进程树，同时默认调用 `ProcessManager.stop_session(session_id)` 停止当前会话的
    后台任务。切换会话只断开旧流时用 `?background=0`，不要误杀旧会话任务。
35. **后台任务完成事件只发一次**：`ProcessManager` 的 watcher 负责设置结束时间、
    关闭 Job Object、发 `process_completed`；用户主动停止时先把 `notify_on_exit`
    设为 false。前端只对 `notify=true` 且属于当前会话的任务触发内部续跑。
36. **内部续跑消息不能显示成用户输入**：`ChatSession.ask_stream(internal=True)`
    会写 `additional_kwargs["alembic_internal"]=True`，`_serialize_messages` 输出
    `internal: true`；前端 `mapHistory` 必须跳过它，否则刷新后会出现假用户消息。
37. **附件只写工作区**：`POST /api/sessions/{id}/attachments` 把原始字节写入
    `<workspace>/.alembic/attachments/<会话ID>/`；聊天时服务端校验路径并构造
    `[Attached files]` 块。不要把附件写到工作区外，否则文件工具无法读取。
38. **命令必须经过 `ToolContext.sandbox`**：`tools/shell.py` 和 `tools/processes.py`
    不允许直接调用 `subprocess.Popen` 执行模型命令。没有沙盒时返回
    `sandbox_unavailable`，不要加宿主机回退。
39. **沙盒用户不是管理员**：`scripts/setup-sandbox.ps1` 创建的两个账号只用于执行命令；
    不要把它们加入 Administrators，也不要在应用进程里保存明文密码。凭据只以 DPAPI
    形式保存到 `data/sandbox-users.json`，文件 ACL 仅当前用户可读。
40. **网络默认最严**：`SandboxPolicy.network` 默认 `offline`；只有 `allowlist` 才启动
    本机代理并注入 `HTTP_PROXY` / `HTTPS_PROXY`。WSL 终端在 Windows 沙盒下必须拒绝。
41. **Docstring / JSDoc 统一英文**：所有 Python 模块、类、函数、方法 docstring，以及
    前端 `/** ... */` JSDoc 都必须用英文。写法以“是什么、怎么用、有什么约束”为主，
    保留 `Args` / `Returns` / `Raises` 等结构化段落；不要写历史原因、第一人称解释或
    与代码无关的背景。标识符、路径、命令和示例输入保持原样；普通 `//` / `#` 注释
    不在此约束内，可以继续用中文。不要借翻译 docstring 的机会改 UI 文案。
42. **提示词统一英文**：`prompts/system.py`、`prompts/react.py`、`agents/context.py`
    的摘要提示词、工具描述与参数描述都用英文。面向模型的指令可以要求“用用户的语言
    回复”，但不能把指令本身写成中文；用户问题、项目上下文、文件内容仍可以是中文。

    工具规则必须根据当前实际可用工具动态生成，不能写死未启用的工具名；项目上下文
    按 `ALEMBIC.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules` 注入，且不能覆盖
    安全、审批和沙盒规则。
43. **面向 AI 的返回值统一英文**：`@tool` 的返回值、`tool_error(...)` 的 `message`
    和 `hint`、沙盒错误、命令策略对模型的拒绝说明、内部续跑提示，都使用英文。
    失败仍保持 `Error: [code] message`，可选第二行提示；`code` 继续用稳定的
    lowercase snake_case，不要因为翻译改错误码。数据本身不翻译：文件内容、文件名、
    用户查询、中文城市名、搜索结果标题、文档正文保持原文。
44. **界面语言与模型语言分开**：前端 UI、CLI 帮助、审批卡片展示文案、日志可以继续
    用中文；但审批硬拒绝进入模型时，中文 `CommandRisk.reason` 不能直接拼进
    `blocked_by_policy`，必须走 `CommandRisk.reason_en`（见
    `tools/command_policy.py` / `agents/approval.py`）。`tools_search` 不得把中文
    工具目录的 `label` / `description` 注入模型返回；只返回工具名和加载状态，
    具体说明来自英文工具 schema。
45. **改动返回值语言必须同步测试**：工具输出语言变化时，更新对应测试断言，并优先
    断言稳定的错误码、英文关键字段或结构；不要在运行时按语言切换返回值。
46. **设置文案只讲“这个选项做什么”**：一行说完，不写背景、意义或宣传语，不用
    “控制…边界”“帮助你…”这类解释腔。控件本身已经说清楚时（例如模型列表左侧箭头
    展开能力参数），删掉说明文字，不要为了填满版面再写一句。下拉框下方的
    `general-hint` 也只补一句差异；工具条目的 `description` 同样保持一句话。
    设置分组标题字号大于行标题，层级从字号就能看出来。
47. **悬停说明标签只有一套**：模板里继续写原生 `title`，由 `GlobalTooltip`
    （挂在 `App.vue`）统一接管为 `.ui-tooltip`：`--card` 底、`--border` 描边、
    11px、圆角与卡片一致，按空间自动上/下展开，鼠标与键盘焦点都触发。
    不要新增组件级的 tooltip 实现或自定义气泡，也不要为 hover 改写模板结构；
    气泡样式只在 `main.css` 的 `.ui-tooltip` 里维护。自带悬停浮层的元素
    （如修改文件的 Diff 预览行）不要再挂 `title`，避免两个浮层叠在一起。
48. **对话列表约定**：标题 / 置顶 / 归档存在 `SessionMeta`（会话 JSON），前端不要
    另存 localStorage；分支必须复制会话文件（`SessionStore.duplicate`），不能只复制
    列表项。对话与工作区菜单统一用 `FloatingMenu` 挂到 body，删除统一用
    `ConfirmDialog` 两次点击确认。标题由首轮结束后的 `_finish_turn` 调摘要模型生成，
    失败回退 `title_from_text`；要调整标题策略就改这两处，不能只改前端。

## 6. 环境坑（这台机器上真实踩过的）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| npm 无法写 `%LOCALAPPDATA%\npm-cache` | 沙箱只允许工作区 | `$env:npm_config_cache=<仓库>\.tmp\npm-cache` |
| `npm install` 末尾 `spawn EPERM` | 受限环境禁止带管道的子进程（lifecycle 脚本） | `npm install --ignore-scripts`；`scripts/dev.ps1` 已内置 |
| `vite build` / `vite dev` 报 `spawn EPERM`（esbuild） | 同上，esbuild 需要子进程 | 在普通终端运行；受限沙箱里需放宽权限 |
| `uv add` 报 lock 文件拒绝访问 | uv 缓存写在 `%LOCALAPPDATA%` | `$env:UV_CACHE_DIR=<工作区>\.tmp\uv-cache` |
| `npm install` 404 / `cdn.npmmirror.com/binaries/npm/...` | registry 被配成二进制镜像 | `--registry=https://registry.npmmirror.com` |
| electron 启动即崩、`app` 未定义 | 继承了 `ELECTRON_RUN_AS_NODE=1` | 启动前 `Remove-Item Env:ELECTRON_RUN_AS_NODE` |
| `memory/` 写入被拒 | 受限沙箱只允许工作区部分路径 | 验证时用 `ALEMBIC_HOME=<工作区>\.tmp\...` |
| 中文测试输出乱码 | Windows 控制台编码 | 不影响结果；需要时 `chcp 65001` |

## 7. 桌面版与安装包

**Electron 已适配 Vue 前端。** `desktop/main.js` 启动 sidecar 后启动
`desktop/frontend-server.js`，由它提供 `resources/frontend` 并把 `/api` 反向代理到
后端；`/?token=` 在静态服务器设置 HttpOnly Cookie，并继续返回页面让前端读取
token。开发时指向 `frontend/dist`，
可用 `ALEMBIC_FRONTEND` 覆盖。`packaging/browser-launcher.cmd` 仍是旧提示文件，
可以忽略或删除。

**安装包**由 `packaging/build-release.ps1` 组装：

1. 同步前端 / desktop 版本；
2. 前端构建；
3. PyInstaller 后端 + 内置运行时；
4. electron-builder 生成 `desktop/dist/win-unpacked`；
5. 生成 NSIS 位图，`electron-builder --win nsis --prepackaged` 打出安装器；
6. 复制到 `build/release/out`，写 `release.json` / `SHA256SUMS.txt`。

安装器是 `perMachine` NSIS，启动即请求管理员；窗口无原生边框，第一页自绘标题栏，
显示安装位置和两个快捷方式选项，安装内容列出主程序、内置运行时和 Windows 命令沙盒
（后两者始终安装）。安装完成后调用
`packaging/install/nsis-postinstall.ps1` 做运行时校验、沙盒、快捷方式、卸载器复制和
注册表 / `install.json`。卸载界面在 `desktop/uninstall.html`，通过
`uninstall-worker.ps1` 提权清理。`-Dev` 只把“开发版”加到安装包 / exe / 安装目录 /
快捷方式名称，页面不显示发布通道。完整说明、版本管理、代码签名和扩展点见
[packaging.md](packaging.md)。

另：`run_command` 是流式执行（当前 Windows 沙盒使用管道），并且只能通过
`ToolContext.sandbox` 启动；停止生成会结束当前前台命令的进程树，并停止当前会话由
`ProcessManager` 管理的后台任务。`wait_process` 仍存在，但常规后台任务应依赖完成事件
和内部续跑，不要用它挂住整轮。

## 8. 还没做的

- 前端组件/单元测试（当前只做 `vue-tsc` + 构建 + 手动验证）；
- 代码签名、自动更新、托盘 / 系统通知；
- 生产部署的独立静态资源服务（桌面版已由 Electron 内置静态服务器解决）；
- Windows 原生 ConPTY（当前 Windows 沙盒用管道）、审批卡片 diff 视图、
  `apply_patch` 的跨文件事务回滚（`batch_edit` 已具备）；
- 文本 ReAct 是单步串行（每轮一个工具），提示词每步重发历史；默认的
  `tool_calling` 可并行返回多个工具调用，延迟更低；
- 文档图片 OCR / 视觉模型、@文件引用与粘贴截图；
- MCP 客户端、skills / 自定义命令；
- 多窗口 / 托盘 / 系统通知。

## 9. 最近几轮做了什么（按时间）

1. 工具注册表单点化 + 统一错误契约 + 文件/命令工具 + workspace 逃逸防护；
2. 工具审批（允许本次 / 始终允许 / 拒绝），网页 SSE 双向回执 + CLI 终端提问；
3. 界面重构：左侧按工作区分组、模型右下角切换、设置入口收敛到左上角；
4. 字段类型（text/number/select/switch）+ 模拟工具 `mock_data`；
5. 数据目录迁到 `%APPDATA%\Alembic`（含一次性迁移）；
6. Electron 外壳 + token 鉴权 + 随机端口 + 打包；
7. **前端整体迁移到 Vue 3 + Vite + Pinia + TypeScript，后端改为 API-only + CORS，
   新增一键开发脚本，桌面/打包暂时搁置（后续已恢复，见第 29 条）。**
8. **新增 `read_document`、`fetch_url`、文件生命周期工具、`todo_write`、
   `ask_user_question`、后台进程工具；网页端增加问答卡与任务清单，
   审批键拆分 delete/move。**
9. **切换为原生 ReAct（`agents/react.py` + 英文 `prompts/react.py`），保留
   `AGENT_ENGINE=tool_calling` 回退；系统/摘要提示词改为英文；测试从 201 增至 284。**
10. **通用设置：工作步骤展示四档、收起时机两项、代码工作视图开关；
    新增 `PreferencesStore` / `/api/preferences`，并补充 `tool_result` SSE 事件。**
11. **项目改名 ALEMBIC：Python 包 `alembic`、前端/桌面/打包名称、API 标题、默认数据目录
    与 `ALEMBIC_*` 环境变量同步；旧 `SmartAgent` 数据目录与旧环境变量保留兼容。**
12. **修复「说已写入但没写文件」：默认引擎改回 `tool_calling`（结构化工具调用），
    文本 ReAct 加 stop 序列 + 严格单步解析 + 协议纠错重试 + 原始输出日志；
    `write_file` 落盘后 stat 校验；`ask_user_question` 超限改为报错而非静默截断；
    两条引擎都能派生 `thought` 工作步骤；测试从 284 增至 291。**
13. **新设备初始化向导：供应商模板（带图标）— 深度求索 / OpenAI / Claude / 千问 /
    硅基流动 / 自定义；设置 → 供应商同步图标选择器；不再内置默认模型名，新增
    `needs_setup`；实时天气默认关闭、API Host 无默认值；tests 从 291 增至 295。**
14. **模型管理改为「供应商 + 模型列表」：一个供应商共用 Base URL/Key，模型列表
    支持上游获取、单独思考等级（none/low/high/max）与温度；输入框模型切换先选
    供应商再列该供应商的模型；初始化向导复用 `ProviderEditor`；旧扁平模型列表
    自动迁移；tests 从 295 增至 298。**
15. **新增 `scripts/reset-config.ps1/.cmd`：清理 Alembic / SmartAgent 配置与仓库
    示例 data，默认保留 memory，支持 DryRun / Backup / IncludeSessions / Force，
    方便重新查看初始化向导。**
16. **修复初始化后首条消息的 422：`ChatPayload.session_id` 允许 null，后端生成
    新的 chat-* 会话；前端 API 错误会展开 FastAPI 的 422 校验详情；tests 从
    298 增至 299。**
17. **新安装不自动建工作区；工作区里直接发送即创建对话；推理等级改为
    「可选集合 + 当前值」：设置里多选（默认 low/high/max），composer 增加
    推理按钮 + 可拖动吸附滑条；测试 299。**
18. **审批/提问移到输入栏；右侧边栏支持工作区文件树、文件预览、内置浏览器与
    最多 3 个标签页；对话内文件可点击预览；轮次结束显示修改文件与 +/- 行数；
    通用设置加入只读 / 工作区内修改 / 完全权限三档；测试 338。**
19. **审批卡片改为「先讲人话、同时保留原文」：`run_command` / `start_process` 新增
    `description` 参数（系统提示词要求必填）作为主说明，模型没填时用
    `tools/command_brief.py` 离线翻译命令（含 `--- 标题 ---`、`foreach`、正则计数）；
    命令拆出分步说明（`steps`），原文默认展开且不再截断到 800 字符；终端提问
    同步显示分步说明；新增 `tests/command_brief_test.py`。**
20. **后台任务面板 + 实时命令：`ProcessManager` 增加状态/时长/完成回调与 Job Object
    停止；`run_command` 改为流式输出，停止生成会结束前台进程树并停止当前会话后台
    任务；新增 `/api/processes` 与 `/api/processes/events`；`start_process(notify=True)`
    完成时通过 SSE 触发内部续跑。输入区新增附件导入、手动压缩与上下文占用圆环；
    `tests/process_view_test.py` 覆盖进程事件、取消、附件与压缩；测试 467。**
21. **Windows 原生命令沙盒：`tools/sandbox/` 统一 `SandboxRunner` 接口，未初始化时
    fail closed；`scripts/setup-sandbox.ps1` 创建离线/允许列表两个标准用户和按用户
    防火墙；`WindowsSandbox` 用专用用户 + ACL + Job Object 启动，`allowlist` 走本机
    CONNECT 代理；新增 `/api/sandbox/status`、`/api/sandbox/config` 和设置面板状态。
    `tools/shell.py` / `tools/processes.py` 已删除宿主机直接执行路径；测试 467。**
22. **思考去重：供应商流式 reasoning 与最终 AIMessage 的空格/换行常不一致，旧逻辑用
    `strip()` / `endswith()` 直接比较，导致同一条思考出现两次。现在后端 `context.py`
    与前端 `chat.ts` 都用“去掉所有空白”的指纹比较；`tests/context_test.py` 增加了
    空格差异回归用例。测试 467。**
23. **原子批量编辑：新增 `batch_edit`（`tools/atomic_edit.py`）。一次提交多文件/多处
    精确替换；先在内存中全部校验，任一失败不落盘；提交阶段失败会回滚已写文件。
    注册表、`BASIC_TOOLS`、审批摘要和审批卡展开项已同步。**
24. **压缩后的可见原文 + 模型高级设置：`SessionStore` 同时保存模型上下文 `messages`
    和用户可见原文 `display_messages`，历史接口读后者；摘要提示词改为英文，用户仍看到
    完整问答。每个模型新增 supports_reasoning / supports_images / context_length /
    max_output_tokens，供应商编辑行左侧箭头展开这些字段与温度、推理等级；上下文阈值
    和 `max_tokens` 按模型生效。`/api/providers/fetch` 会解析 OpenRouter 风格的
    context_length / max_completion_tokens / input_modalities / supported_parameters，
    没有元数据时回退 `default_model_limits()`。测试 467。**
25. **面向模型的英文输出：所有 `@tool` 返回值、`tool_error` 消息/提示、工具参数说明和
    Python docstring 已改为英文；前端 JSDoc 同步为英文。UI 标签、CLI 提示、审批卡片
    和日志仍保留中文。`tools_search` 不再把中文目录标签混进模型返回值。测试 467。**
26. **设置文案与悬停说明统一：设置各面板的说明改为一句话功能描述，控件本身能说清的
    直接删除（模型列表）；设置分组标题字号加大到行标题之上；悬停说明由
    `GlobalTooltip` 统一接管原生 `title`，样式收敛到 `.ui-tooltip`；模型能力箭头与
    新建标签页加号改用 SVG 居中；工具长文本域只允许竖向拉伸；工具条目描述收短。**
27. **左侧栏与对话管理重构：顶部改为视图选项 / 搜索 / 添加工作区三个图标按钮；
    支持按工作区 / 单列表分组、最近更新 / 手动排序、拖动排序插入线、置顶规则、
    重命名、分支（原标题(n)）、归档与「已归档」区、相对时间、hover 操作图标；
    对话与工作区菜单改为挂 body 的浮层；删除改为居中两次确认。`SessionMeta` 新增
    title / pinned / archived，新增 `PATCH /api/sessions/{id}` 与分支接口；标题由模型
    在首轮结束后生成，失败回退首条内容。**
28. **工作区选择改为盘符起始、地址栏图标式上一级 / 前往 / 资源管理器选择、网格
    九列与列表视图；新增图片文件预览（PNG/JPEG/GIF/WebP/SVG）；设置 → 工具加入
    网格 / 列表视图；左右侧栏开关与布局过渡、搜索展开动画、空目录提示对齐、
    图标 hover 与确认弹窗样式统一。**
29. **桌面版恢复 + Windows 安装包：Electron 静态服务器提供 Vue 构建并代理
    `/api`；提权 worker 自动创建 Windows 命令沙盒、校验内置 Node/Python/pnpm、
    写快捷方式与卸载项；`packaging/build-release.ps1` 统一版本和 SHA256 校验和。
    详见 [packaging.md](packaging.md)。**
30. **安装器改为原生 NSIS：** 安装器窗口无原生边框，选项页自绘纸色标题栏
    （最小化 / 关闭），显示安装位置和桌面 / 开始菜单两个快捷方式选项；运行时与
    Windows 命令沙盒固定安装；安装器和主程序默认管理员。
    主窗口改为无边框自定义标题栏（不含 ALEMBIC，含关于 / 最小化 / 最大化还原 / 关闭），
    关于窗口显示名称与版本；对话和左右侧栏加入最小宽度和窗口缩放钳制；`-Dev` 只在
    安装包 / exe / 安装目录名称后加“开发版”，页面不标注发布通道；启动时先显示外壳、
    后端惰性导入、关闭 GPU 合成。

## 当前扩展与运行时验证

本轮完整后端测试运行 631 项，结果通过（其中 2 项因宿主无法创建符号链接而跳过）；前端 `vue-tsc` 类型检查及 Vite 生产构建通过。实际原生命令沙盒在不依赖宿主 PATH 的条件下验证了内置 Node、Python、venv/pip 和 pnpm。真实 PyInstaller 打包及冻结产物冒烟检查通过：在隔离的 `ALEMBIC_HOME` 中启动后，`/api/extensions` 返回已启用的内置 `code-review`、正确的打包资源路径及空错误列表。MCP 已验证本地 SDK 传输，不将其视为外部身份提供商 OAuth 授权的端到端验证。

## 10. 给接手者的建议顺序

1. 跑一遍后端测试并检查失败与跳过原因（本轮基线见上），再 `cd frontend && npm run build`；
2. 读 `web/runtime.py`（默认 `create_agent`）、`agents/react.py`（可选文本 ReAct）、
   `tools/registry.py`、`web/server.py`、`agents/context.py`，够理解后端 85%；
3. 读 `frontend/src/stores/chat.ts`、`frontend/src/api/chat.ts`、`frontend/src/App.vue`，
   理解前端状态与 SSE；
4. 用 `.\scripts\dev.ps1` 起服务，手动点一遍界面（左侧工作区、右下角模型、设置里工具/审批）；
   首次启动会自动进入初始化向导：选供应商模板 → 填模型/密钥 → 设置工作步骤；
5. 要加工具：写 `tools/xxx.py` + 注册表条目即可，前端设置面板按字段自动派生；
6. 改任何写入路径前，先确认 `settings.app_home`，别把用户数据写回代码目录；
7. 不要为了让桌面版「先跑起来」而把静态资源重新塞回 FastAPI —— 桌面版由
   `desktop/frontend-server.js` 提供静态资源并代理 `/api`，后端保持 API-only；
8. 打包前先读 [packaging.md](packaging.md)；改安装步骤时同步
   `packaging/nsis/installer.nsh`、`packaging/install/nsis-postinstall.ps1` 和文档。
