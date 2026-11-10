# ALEMBIC 交接文档（HANDOVER）

> 给接手的智能体 / 开发者：先读这一份，再动代码。
> 最后更新：2026-11-07，默认引擎改回 `tool_calling`（结构化工具调用），原生 ReAct
> 文本循环保留为可选模式并做了协议硬化（stop 序列 / 严格单步解析 / 纠错重试）；
> 338 个后端测试全绿。

## 1. 这是什么

一个**本地优先的智能体工作区**：

- 后端：Python 3.11 + FastAPI + LangChain 1.4.8，默认走 `create_agent` 的
  **模型原生工具调用**（结构化 `tool_calls`，观测只能由运行时产生）；设置
  `AGENT_ENGINE=react` 可切到 `agents/react.py` 的文本 ReAct 循环（已硬化）。
  模型供应商支持深度求索 / OpenAI / Claude / 千问 / 硅基流动 / 自定义，统一走
  OpenAI 兼容接口（Claude 走 Anthropic 兼容端点）；一个供应商下可挂多个模型，
  每个模型有独立的可选推理等级集合（none/low/high/max）、当前等级与温度；
- 首次启动且没有可用供应商时，前端显示**初始化向导**：选择带图标的供应商模板、
  维护模型列表、填写 Base URL / API Key，再设置工作步骤展示与收起时机；
- 前端：Vue 3.5 + Vite 7 + TypeScript + Pinia（`frontend/`）；
- 两者通过 `/api` 通信：开发时前端跑 5173，后端跑 8000，Vite 代理 `/api`；
- 后端**不再提供 HTML / 静态资源**，只提供 JSON 与 SSE；
- Electron 外壳（`desktop/`）与 PyInstaller 打包（`packaging/`）**暂未适配**
  这次的前后端分离，恢复桌面版时需要单独改造。

数据（模型配置、工具开关、工作区、会话历史）全部存放在用户目录，
`%APPDATA%\Alembic\{data,memory}`，不在代码目录里。旧的 `%APPDATA%\SmartAgent`
会在首次启动时由 `config/storage.py` 复制到新目录，原目录保留。

> 注意区分两个目录：`%APPDATA%\Alembic` 是**业务数据**（Python 后端写）；
> `%LOCALAPPDATA%\Alembic` 是 **Electron 自己的 profile**（缓存、`runtime/*.log`，
> 当前桌面版暂停，后者暂时用不到）。

## 2. 30 秒上手

```powershell
uv sync                                              # 建 .venv、装 Python 依赖
cd frontend; npm install; cd ..                      # 装前端依赖

.\scripts\dev.ps1                                    # 后端 8000 + 前端 5173，自动开浏览器

# 或者两个终端：
.\.venv\Scripts\python.exe -m web                    # http://127.0.0.1:8000/api
cd frontend; npm run dev                             # http://127.0.0.1:5173
```

验证：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"   # 338 个测试
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
  react.py               可选文本 ReAct 循环：stop 序列、严格单步解析、审批、限流、流式事件
  context.py             ChatSession、SessionStore、上下文压缩、SessionMeta
  approval.py            审批策略、中间件、终端提问器
config/
  settings.py            环境变量 + app_home 解析 + 前端地址 / CORS 来源 + AGENT_ENGINE
  storage.py             ensure_storage：首次运行把仓库 data/ memory/ 迁到 app_home
  store.py               ModelStore / ToolStore / WorkspaceStore / PreferencesStore + 迁移
prompts/system.py        英文系统提示词（工作区边界、工具原则、失败处理）
prompts/react.py         英文 ReAct 格式与工具目录模板
tools/
  registry.py            ★ 工具清单唯一事实来源：TOOL_SPECS / ToolField / build_tools
  context.py             ToolContext（workspace、输出预算、会话引用、提问通道、进程管理器、视觉通道）
  errors.py              统一错误契约 tool_error() / truncate()
  paths.py               工作区路径解析与逃逸防护
  files.py               list_dir / read_file / search_files / find_files / file_stat
                         write_file / edit_file / make_dir / delete_file / move_file / copy_file
  documents.py           read_document（PDF / DOCX / XLSX / PPTX / 文本，按字符偏移分段）
  images.py              read_image（把图片交给当前视觉模型，返回文字描述）
  fetch.py               fetch_url（SSRF 防护、重定向校验、HTML 转文本）
  search.py              web_search（Bing / DuckDuckGo / Tavily / Brave / Serper / SearXNG）
  download.py            download_file（流式下载、SSRF、大小上限、.part 保护）
  patch.py               apply_patch（标准 unified diff，多文件 / 多 hunk）
  git.py                 git_status / git_diff / git_commit（参数列表调用，不走 shell）
  todo.py                todo_write + TodoStore（会话级任务清单）
  questions.py           ask_user_question / Question / 终端提问通道
  processes.py           start_process / list_processes / read_process_logs / wait_process / stop_process
  shell.py               run_command（超时、输出截断、退出码）
  time.py weather.py mock.py
web/
  __main__.py            自己 bind socket（支持 port=0）、写端口文件、ensure_storage、跑 uvicorn
  server.py              只提供 /api：路由、SSE 对话、审批、工作区、供应商、CORS
  auth.py                可选 token 中间件（Bearer / Cookie；放行 OPTIONS 与 /api/health）
  approvals.py           ApprovalRegistry / ApprovalBroker（SSE ↔ HTTP 回执）
  questions.py           QuestionRegistry / QuestionBroker（ask_user_question 的 SSE ↔ HTTP 回执）
  runtime.py             build_runtime：按当前设置装配 agent
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
    components/          侧边栏、对话流、审批卡、问答卡、任务清单、工作步骤、代码工作视图、通用设置、输入区、设置、初始化向导、供应商编辑器/图标/选择器、Toast
    lib/
      markdown.ts        轻量 Markdown 渲染（从旧 app.js 迁移，无第三方依赖）
      providers.ts       ★ 供应商模板唯一事实来源：名称 / Base URL / 图标
      preferences.ts     工作步骤展示与收起时机的选项（通用设置 + 初始化向导共用）
    stores/
      workspace.ts       Pinia：模型 / 工具 / 工作区 / 会话列表 / 通用设置与 CRUD
      chat.ts            当前会话、流式消息、审批/提问回执、任务清单、停止生成
      ui.ts              弹窗、搜索、折叠状态、Toast、推荐问题草稿
    assets/main.css      设计系统（纸白 + 薄荷绿）
scripts/
  dev.ps1 / dev.cmd      一键启动本地开发环境
  reset-config.ps1/.cmd  清除 Alembic / SmartAgent 配置，重新查看初始化向导
desktop/                 Electron 外壳（旧版，未适配 Vue 前端）
packaging/               PyInstaller（已移除 web/static；桌面打包未适配）
tests/                   *_test.py，全部离线（不调真实模型）
```

## 4. 关键数据流

### 4.1 配置 → 工具

```
data/tools.json → ToolStore.configs() → build_runtime(..., tool_configs=)
→ build_tools(enabled, configs, ctx) → spec.builder(config, ctx) → LangChain tools
```

- 条目的启用状态与参数都在 `data/tools.json`，结构版本写在 `version` 字段（当前 2）。
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

事件：`meta` / `delta` / `thought` / `tool` / `tool_result` / `approval` /
`approval_expired` / `question` / `question_expired` / `todo` / `compacted` / `done` / `error`。

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
  （可多选）与当前 `thinking`（none/low/high/max），以及 temperature；
  `active_provider_id` + `active_model_id` 指向当前模型。
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
- `permission_mode` 决定审批范围：`read_only` 全部工具、`workspace_write` 只审批
  执行命令、`full_access` 不审批；服务端在每轮聊天开始时按它构造 `ApprovalGate`。
- 审批 / 提问卡渲染在 `ChatComposer`，待处理时替换输入栏；助手消息只保留已处理结果。
- 右侧边栏 `InspectorSidebar` 通过 `/api/workspace/files` 与 `/api/workspace/file`
  浏览工作区；标签页最多 3 个。文件树、预览、浏览器都是独立组件。
- 文件工具的返回值带 `变更：+N -M`，`TurnSummary` 据此在完成轮次后汇总修改文件。
- `AssistantTurn.workEvents` 按发生顺序保存 `thought` 与 `tool` 事件；
  `tool_result` 按 `tool_call_id` 回填到对应工具事件，而不是单独堆在末尾。
- `tool_result` 事件让工作步骤在流式期间就能显示结果；历史会话通过
  `ToolMessage.tool_call_id` 与 `AIMessage.tool_calls[].id` 配对。

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

## 7. 已知问题（最重要的一条）

**Electron / 打包尚未适配 Vue 前端。** 后端根地址现在返回 API 说明 JSON，
而 `desktop/main.js` 仍然 loadURL 这个地址，所以桌面版会显示 JSON 而不是界面；
`packaging/browser-launcher.cmd` 已改成提示信息。恢复时的方向：

1. 前端 `npm run build` 产出 `frontend/dist`；
2. 由 Electron 或一个独立的静态服务器提供 `dist`（SPA 需要 `/` 回退到 `index.html`）；
3. 开发模式可让 Electron 指向 `http://127.0.0.1:5173`，生产模式指向打包后的静态目录；
4. 后端继续只跑 `/api`，保留 token / 随机端口 / sidecar 逻辑。

另：`run_command` 是一次性抓完整输出，不是流式 PTY；后台长任务请用
`start_process`。停止生成只断开前端 SSE，后端仍会跑到当前模型调用边界。

## 8. 还没做的

- 前端组件/单元测试（当前只做 `vue-tsc` + 构建 + 手动验证）；
- Electron 适配、安装包、代码签名、应用图标；
- 生产部署的静态资源服务（Nginx / Caddy / 一个独立静态服务器）；
- 流式 PTY 终端、审批卡片 diff 视图、任务真取消；
- 文本 ReAct 是单步串行（每轮一个工具），提示词每步重发历史；默认的
  `tool_calling` 可并行返回多个工具调用，延迟更低；
- `web_search`（当前只有 `fetch_url`；搜索需要接第三方检索源）；
- 文档图片 OCR / 视觉模型、附件上传与 @文件引用；
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
   新增一键开发脚本，桌面/打包暂时搁置。**
8. **新增 `read_document`、`fetch_url`、文件生命周期工具、`todo_write`、
   `ask_user_question`、后台进程工具；网页端增加问答卡与任务清单，
   审批键拆分 delete/move。**
9. **切换为原生 ReAct（`agents/react.py` + 英文 `prompts/react.py`），保留
   `AGENT_ENGINE=tool_calling` 回退；系统/摘要提示词改为英文；测试从 201 增至 284。**
10. **通用设置（按 DSH 设计）：工作步骤展示四档、收起时机两项、代码工作视图开关；
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

## 10. 给接手者的建议顺序

1. 跑一遍后端测试，确认 338 全绿；再 `cd frontend && npm run build`；
2. 读 `web/runtime.py`（默认 `create_agent`）、`agents/react.py`（可选文本 ReAct）、
   `tools/registry.py`、`web/server.py`、`agents/context.py`，够理解后端 85%；
3. 读 `frontend/src/stores/chat.ts`、`frontend/src/api/chat.ts`、`frontend/src/App.vue`，
   理解前端状态与 SSE；
4. 用 `.\scripts\dev.ps1` 起服务，手动点一遍界面（左侧工作区、右下角模型、设置里工具/审批）；
   首次启动会自动进入初始化向导：选供应商模板 → 填模型/密钥 → 设置工作步骤；
5. 要加工具：写 `tools/xxx.py` + 注册表条目即可，前端设置面板按字段自动派生；
6. 改任何写入路径前，先确认 `settings.app_home`，别把用户数据写回代码目录；
7. 不要为了让桌面版「先跑起来」而把静态资源重新塞回 FastAPI —— 先想清楚部署形态。
