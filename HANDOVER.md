# 交接文档（HANDOVER）

> 给接手的智能体 / 开发者：先读这一份，再动代码。
> 最后更新：2026-11-05，前端已重构为 Vue 3 + Vite，前后端分离；201 个后端测试全绿。

## 1. 这是什么

一个**本地优先的智能体工作区**：

- 后端：Python 3.11 + FastAPI + LangChain 1.4.8（`create_agent`），模型走 DeepSeek /
  OpenAI 兼容接口；
- 前端：Vue 3.5 + Vite 7 + TypeScript + Pinia（`frontend/`）；
- 两者通过 `/api` 通信：开发时前端跑 5173，后端跑 8000，Vite 代理 `/api`；
- 后端**不再提供 HTML / 静态资源**，只提供 JSON 与 SSE；
- Electron 外壳（`desktop/`）与 PyInstaller 打包（`packaging/`）**暂未适配**
  这次的前后端分离，恢复桌面版时需要单独改造。

数据（模型配置、工具开关、工作区、会话历史）全部存放在用户目录，
`%APPDATA%\SmartAgent\{data,memory}`，不在代码目录里。

> 注意区分两个目录：`%APPDATA%\SmartAgent` 是**业务数据**（Python 后端写）；
> `%LOCALAPPDATA%\SmartAgent` 是 **Electron 自己的 profile**（缓存、`runtime/*.log`，
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
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"   # 201 个测试
cd frontend
npm run build                                        # vue-tsc + vite build
```

命令行的用法没有变化：`.\\.venv\\Scripts\\python.exe main.py "北京现在几点？"`。

## 3. 目录与职责

```
main.py                  CLI 入口（argparse + ChatCLI，含 /approvals 等命令）
run_backend.py           PyInstaller 入口，等价于 python -m web
agents/
  agent.py               CLI 侧的 model / tools / middleware 组装
  context.py             ChatSession、SessionStore、上下文压缩、SessionMeta
  approval.py            审批策略、中间件、终端提问器
config/
  settings.py            环境变量 + app_home 解析 + 前端地址 / CORS 来源
  storage.py             ensure_storage：首次运行把仓库 data/ memory/ 迁到 app_home
  store.py               ModelStore / ToolStore / WorkspaceStore + 数据结构迁移
prompts/system.py        系统提示词（含工作区边界与错误处理约定）
tools/
  registry.py            ★ 工具清单唯一事实来源：TOOL_SPECS / ToolField / build_tools
  context.py             ToolContext（workspace、输出预算、命令超时）
  errors.py              统一错误契约 tool_error() / truncate()
  paths.py               工作区路径解析与逃逸防护
  files.py               list_dir / read_file / search_files / write_file / edit_file
  shell.py               run_command（超时、输出截断、退出码）
  time.py weather.py mock.py
web/
  __main__.py            自己 bind socket（支持 port=0）、写端口文件、ensure_storage、跑 uvicorn
  server.py              只提供 /api：路由、SSE 对话、审批、工作区、模型导入、CORS
  auth.py                可选 token 中间件（Bearer / Cookie；放行 OPTIONS 与 /api/health）
  approvals.py           ApprovalRegistry / ApprovalBroker（SSE ↔ HTTP 回执）
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
    components/          侧边栏、对话流、审批卡、输入区、设置、工作区浏览、Toast
    lib/markdown.ts      轻量 Markdown 渲染（从旧 app.js 迁移，无第三方依赖）
    stores/
      workspace.ts       Pinia：模型 / 工具 / 工作区 / 会话列表与 CRUD
      chat.ts            当前会话、流式消息、审批回执、停止生成
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

### 4.2 对话 → SSE

```
Vue fetch POST /api/chat
  → Vite dev proxy → FastAPI
  → 服务端起工作线程跑 ChatSession.ask_stream，事件推到 queue.Queue
  → 主线程从队列取出后写 SSE
  → frontend/src/api/chat.ts 按 "\n\n" 切帧 → chat store 更新当前 assistant turn
```

事件：`meta` / `delta` / `tool` / `approval` / `approval_expired` / `compacted` / `done` / `error`。

> **不要改回「生成器里直接跑 agent」**。审批需要工具线程阻塞等待浏览器回执，
> 生成器一旦阻塞就发不出 `approval` 事件。

> 前端流式渲染用 `requestAnimationFrame` 合并高频 `delta`，不要每帧重写整篇 Markdown。

### 4.3 审批

```
ApprovalMiddleware.wrap_tool_call
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

### 4.4 前端状态

```
App.vue onMounted
  → GET /api/bootstrap → workspace store（models/tools/workspaces/sessions）
  → 打开第一个会话 → GET /api/sessions/{id}/messages
  → history 消息按「user 一轮 / assistant+tool 聚合」映射为 ChatTurn[]
  → 后续 POST /api/chat 的 SSE 事件直接改当前 assistant turn
```

- 会话切换时 `chat.ts` 会递增内部 `activeStream`，旧流的回调不会污染新会话。
- 左栏搜索同时匹配工作区名与对话标题；工作区名命中时展示该工作区全部对话。
- 旧的会话没有 `workspace_id` 时，前端按「归到第一个工作区」显示（与后端兼容策略一致）。

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

## 6. 环境坑（这台机器上真实踩过的）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| npm 无法写 `%LOCALAPPDATA%\npm-cache` | 沙箱只允许工作区 | `$env:npm_config_cache=<仓库>\.tmp\npm-cache` |
| `npm install` 末尾 `spawn EPERM` | 受限环境禁止带管道的子进程（lifecycle 脚本） | `npm install --ignore-scripts`；`scripts/dev.ps1` 已内置 |
| `vite build` / `vite dev` 报 `spawn EPERM`（esbuild） | 同上，esbuild 需要子进程 | 在普通终端运行；受限沙箱里需放宽权限 |
| `uv add` 报 lock 文件拒绝访问 | uv 缓存写在 `%LOCALAPPDATA%` | `$env:UV_CACHE_DIR=<工作区>\.tmp\uv-cache` |
| `npm install` 404 / `cdn.npmmirror.com/binaries/npm/...` | registry 被配成二进制镜像 | `--registry=https://registry.npmmirror.com` |
| electron 启动即崩、`app` 未定义 | 继承了 `ELECTRON_RUN_AS_NODE=1` | 启动前 `Remove-Item Env:ELECTRON_RUN_AS_NODE` |
| `memory/` 写入被拒 | 受限沙箱只允许工作区部分路径 | 验证时用 `SMART_AGENT_HOME=<工作区>\.tmp\...` |
| 中文测试输出乱码 | Windows 控制台编码 | 不影响结果；需要时 `chcp 65001` |

## 7. 已知问题（最重要的一条）

**Electron / 打包尚未适配 Vue 前端。** 后端根地址现在返回 API 说明 JSON，
而 `desktop/main.js` 仍然 loadURL 这个地址，所以桌面版会显示 JSON 而不是界面；
`packaging/browser-launcher.cmd` 已改成提示信息。恢复时的方向：

1. 前端 `npm run build` 产出 `frontend/dist`；
2. 由 Electron 或一个独立的静态服务器提供 `dist`（SPA 需要 `/` 回退到 `index.html`）；
3. 开发模式可让 Electron 指向 `http://127.0.0.1:5173`，生产模式指向打包后的静态目录；
4. 后端继续只跑 `/api`，保留 token / 随机端口 / sidecar 逻辑。

另：`run_command` 是一次性抓完整输出，不是流式 PTY；停止生成只断开前端 SSE，
后端仍会跑到当前模型调用边界。

## 8. 还没做的

- 前端组件/单元测试（当前只做 `vue-tsc` + 构建 + 手动验证）；
- Electron 适配、安装包、代码签名、应用图标；
- 生产部署的静态资源服务（Nginx / Caddy / 一个独立静态服务器）；
- 流式 PTY 终端、审批卡片 diff 视图、任务真取消；
- MCP 客户端、skills / 自定义命令、附件与 @文件引用；
- 多窗口 / 托盘 / 系统通知。

## 9. 最近几轮做了什么（按时间）

1. 工具注册表单点化 + 统一错误契约 + 文件/命令工具 + workspace 逃逸防护；
2. 工具审批（允许本次 / 始终允许 / 拒绝），网页 SSE 双向回执 + CLI 终端提问；
3. 界面重构：左侧按工作区分组、模型右下角切换、设置入口收敛到左上角；
4. 字段类型（text/number/select/switch）+ 模拟工具 `mock_data`；
5. 数据目录迁到 `%APPDATA%\SmartAgent`（含一次性迁移）；
6. Electron 外壳 + token 鉴权 + 随机端口 + 打包；
7. **前端整体迁移到 Vue 3 + Vite + Pinia + TypeScript，后端改为 API-only + CORS，
   新增一键开发脚本，桌面/打包暂时搁置。**

## 10. 给接手者的建议顺序

1. 跑一遍后端测试，确认 201 全绿；再 `cd frontend && npm run build`；
2. 读 `tools/registry.py`、`web/server.py`、`agents/context.py` 三个文件，够理解后端 80%；
3. 读 `frontend/src/stores/chat.ts`、`frontend/src/api/chat.ts`、`frontend/src/App.vue`，
   理解前端状态与 SSE；
4. 用 `.\scripts\dev.ps1` 起服务，手动点一遍界面（左侧工作区、右下角模型、设置里工具/审批）；
5. 要加工具：写 `tools/xxx.py` + 注册表条目即可，前端设置面板按字段自动派生；
6. 改任何写入路径前，先确认 `settings.app_home`，别把用户数据写回代码目录；
7. 不要为了让桌面版「先跑起来」而把静态资源重新塞回 FastAPI —— 先想清楚部署形态。
