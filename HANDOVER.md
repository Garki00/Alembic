# ALEMBIC 交接文档（HANDOVER）

> 给接手的智能体 / 开发者：先读这一份，再动代码。
> 最后更新：2026-11-07，切换为原生 ReAct 文本循环，提示词改为英文；新增
> 工作步骤展示 / 收起时机 / 代码工作视图三个通用设置；284 个后端测试全绿。

## 1. 这是什么

一个**本地优先的智能体工作区**：

- 后端：Python 3.11 + FastAPI + LangChain 1.4.8，默认走 `agents/react.py` 的
  **原生 ReAct 文本循环**；设置 `AGENT_ENGINE=tool_calling` 可切回
  `create_agent` 的模型原生工具调用。模型走 DeepSeek / OpenAI 兼容接口；
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"   # 284 个测试
cd frontend
npm run build                                        # vue-tsc + vite build
```

命令行的用法没有变化：`.\\.venv\\Scripts\\python.exe main.py "北京现在几点？"`。

## 3. 目录与职责

```
main.py                  CLI 入口（argparse + ChatCLI，含 /approvals 等命令）
run_backend.py           PyInstaller 入口，等价于 python -m web
agents/
  agent.py               CLI 侧装配：model / tools / engine 选择（react 或 tool_calling）
  react.py               ★ 原生 ReAct 循环：提示词、解析、工具执行、审批、限流、流式事件
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
  context.py             ToolContext（workspace、输出预算、会话引用、提问通道、进程管理器）
  errors.py              统一错误契约 tool_error() / truncate()
  paths.py               工作区路径解析与逃逸防护
  files.py               list_dir / read_file / search_files / file_stat
                         write_file / edit_file / make_dir / delete_file / move_file / copy_file
  documents.py           read_document（PDF / DOCX / XLSX / PPTX / 文本，按字符偏移分段）
  fetch.py               fetch_url（SSRF 防护、重定向校验、HTML 转文本）
  todo.py                todo_write + TodoStore（会话级任务清单）
  questions.py           ask_user_question / Question / 终端提问通道
  processes.py           start_process / list_processes / read_process_logs / stop_process
  shell.py               run_command（超时、输出截断、退出码）
  time.py weather.py mock.py
web/
  __main__.py            自己 bind socket（支持 port=0）、写端口文件、ensure_storage、跑 uvicorn
  server.py              只提供 /api：路由、SSE 对话、审批、工作区、模型导入、CORS
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
    components/          侧边栏、对话流、审批卡、问答卡、任务清单、工作步骤、代码工作视图、通用设置、输入区、设置、Toast
    lib/markdown.ts      轻量 Markdown 渲染（从旧 app.js 迁移，无第三方依赖）
    stores/
      workspace.ts       Pinia：模型 / 工具 / 工作区 / 会话列表 / 通用设置与 CRUD
      chat.ts            当前会话、流式消息、审批/提问回执、任务清单、停止生成
      ui.ts              弹窗、搜索、折叠状态、Toast、推荐问题草稿
    assets/main.css      设计系统（纸白 + 薄荷绿）
scripts/
  dev.ps1 / dev.cmd      一键启动本地开发环境
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

### 4.2 ReAct 一轮

```
ChatSession.ask_stream
  → ReActAgent._run(messages)
     → build_react_prompt(history, question, scratchpad)   # 英文模板
     → model.invoke([SystemMessage, HumanMessage])
     → 有 Final Answer → 追加 AIMessage(final)，结束
     → 有 Action/Action Input
          → ApprovalGate（write/exec 才拦截）
          → tool.invoke(args) → ToolMessage(observation)
          → scratchpad += Thought/Action/Action Input/Observation
          → 下一轮模型调用
```

- `ReActAgent` 同时实现 `invoke` 与 `stream`，因此 CLI、SSE、上下文压缩、历史持久化
  不需要知道底层是 ReAct 还是 tool-calling。
- 中间 `Thought` 通过独立的 `thought` 事件推给前端，作为工作步骤预览；
  最终 `done` 只包含 `Final Answer`，不会把思考混进回答正文。
- `AGENT_ENGINE=tool_calling` 时改走 `create_agent`，审批仍由 `ApprovalMiddleware` 处理。

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
  → GET /api/bootstrap → workspace store（models/tools/workspaces/sessions/preferences）
  → 打开第一个会话 → GET /api/sessions/{id}/messages
  → history 消息按「user 一轮 / assistant+tool 聚合」映射为 ChatTurn[]
  → 后续 POST /api/chat 的 SSE 事件直接改当前 assistant turn
```

- 会话切换时 `chat.ts` 会递增内部 `activeStream`，旧流的回调不会污染新会话。
- 左栏搜索同时匹配工作区名与对话标题；工作区名命中时展示该工作区全部对话。
- 旧的会话没有 `workspace_id` 时，前端按「归到第一个工作区」显示（与后端兼容策略一致）。
- 通用设置保存在 `data/preferences.json`，后端通过 `GET/PUT /api/preferences` 读写；
  前端 `workspace.preferences` 是唯一数据源，`WorkSteps` / `CodeWorkView` 只读它。
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
20. **默认引擎是原生 ReAct**：改 `agents/react.py` 时必须保持 `invoke` / `stream`
    接口与现有 `ChatSession` 兼容；`AGENT_ENGINE=tool_calling` 只作为回退路径。
21. **通用设置只有一处**：后端 `config/store.py: PreferencesStore`，前端
    `workspace.preferences`；不要在组件里另存 localStorage，否则刷新/换浏览器会分叉。
22. **工作步骤策略**：`compact / standard / detailed / verbose` 四档、`completion /
    next_input` 两种收起时机；`verbose` 不自动收起，手动展开的轮次不被强制收起。
23. **工具结果必须带 `tool_call_id`**：`tool` 与 `tool_result` 事件靠 id 配对；
    历史消息也通过 `AIMessage.tool_calls[].id` / `ToolMessage.tool_call_id` 保持配对。
24. **工作步骤按 `workEvents` 顺序渲染**：思考与工具调用交错，不能把所有思考
    单独堆在顶部；每行先给动作标签 + 一行摘要，详情按需展开。

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
- 原生 ReAct 是单步串行（每轮一个工具），提示词每步重发历史；追求低延迟/并行
  工具调用时可切 `AGENT_ENGINE=tool_calling`；
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

## 10. 给接手者的建议顺序

1. 跑一遍后端测试，确认 284 全绿；再 `cd frontend && npm run build`；
2. 读 `agents/react.py`、`tools/registry.py`、`web/server.py`、`agents/context.py` 四个文件，够理解后端 85%；
3. 读 `frontend/src/stores/chat.ts`、`frontend/src/api/chat.ts`、`frontend/src/App.vue`，
   理解前端状态与 SSE；
4. 用 `.\scripts\dev.ps1` 起服务，手动点一遍界面（左侧工作区、右下角模型、设置里工具/审批）；
5. 要加工具：写 `tools/xxx.py` + 注册表条目即可，前端设置面板按字段自动派生；
6. 改任何写入路径前，先确认 `settings.app_home`，别把用户数据写回代码目录；
7. 不要为了让桌面版「先跑起来」而把静态资源重新塞回 FastAPI —— 先想清楚部署形态。
