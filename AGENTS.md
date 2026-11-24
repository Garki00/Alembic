# ALEMBIC 仓库约定

## 核心原则

- 以当前检出的源码、`pyproject.toml`、`frontend/package.json`、`desktop/package.json` 和实际脚本为准。文档与实现不一致时，先核对实现，再同步文档。
- 行为改动先补测试，再改实现；后端测试全部离线，不调用真实模型、不访问外网。
- 先定位再修改。结合源码、日志与可复现步骤区分「已确认原因」和「待验证假设」，不按时间或模块接近推断根因。
- 保留与任务无关的本地改动；不恢复已删除的模块、旧入口或旧配置字段。
- 用户数据只写入 `settings.app_home`。安装后的代码目录可能只读，任何写入都走数据目录。
- 面向模型的文本（提示词、工具描述、工具返回值、Python docstring、前端 JSDoc）用英文；界面文案、CLI 提示、日志用中文。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | CLI 入口（argparse + `ChatCLI`），含 `/status`、`/compact`、`/approvals` 等命令 |
| `run_backend.py` | PyInstaller 入口，等价于 `python -m web` |
| `agents/` | 模型/工具/引擎装配、会话与压缩、审批、用量与缓存、可选文本 ReAct、`agents/subagents/` 子代理 |
| `config/` | 环境变量与路径解析、数据目录迁移、供应商/工具/工作区/偏好持久化 |
| `prompts/` | 分层系统提示词、项目上下文加载、ReAct 模板、工具描述 |
| `tools/` | 工具实现与注册表、路径与命令策略、终端与进程、`tools/sandbox/` 命令沙盒 |
| `web/` | FastAPI：`/api` 路由、SSE、审批与提问中继、后台任务事件、鉴权 |
| `extensions/` | 原生插件、MCP 服务器、Skills 的发现与配置 |
| `frontend/` | Vue 3 + Vite + TypeScript + Pinia 界面 |
| `desktop/` | Electron 外壳：窗口、静态前端服务器、`/api` 反代、sidecar 生命周期、卸载界面 |
| `packaging/` | PyInstaller spec、发布编排、NSIS 脚本与安装位图、可移植运行时锁 |
| `scripts/` | 开发启动、命令沙盒初始化/校验/隐藏、配置重置、运行时下载 |
| `tests/` | `*_test.py`，全部离线 |
| `docs/` | 扩展、运行时、打包等专题说明 |
| `utils/` | 通用并行/缓存/性能辅助 |

`runtimes/`、`dist/`、`build/`、`desktop/dist/`、`.venv/`、`.tmp/` 是构建产物或本地缓存，不提交。

## 常用命令

从仓库根目录执行：

| 用途 | 命令 |
| --- | --- |
| 安装 Python 依赖 | `uv sync` |
| 安装前端依赖 | `cd frontend; npm install; cd ..` |
| 一键开发（后端 8000 + 前端 5173） | `.\scripts\dev.ps1` |
| 仅后端 | `.\.venv\Scripts\python.exe -m web` |
| 仅前端 | `cd frontend; npm run dev` |
| 前端类型检查与生产构建 | `cd frontend; npm run build`（`vue-tsc --noEmit && vite build`） |
| 后端测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"` |
| 命令沙盒初始化（管理员，仅一次） | `.\scripts\setup-sandbox.ps1` |
| 沙盒校验 / 卸载 | `.\scripts\setup-sandbox.ps1 -Verify` / `-Uninstall` |
| 沙盒账户在登录界面隐藏 / 恢复 | `.\scripts\hide-sandbox-users.ps1` / `-Show` |
| 可移植运行时下载 / 离线校验 | `.\.venv\Scripts\python.exe scripts\provision_runtimes.py` / `--offline --verify` |
| 重置配置（保留会话） | `.\scripts\reset-config.ps1 -DryRun` / `-Backup` / `-IncludeSessions` / `-Force` |
| 构建 Windows 安装包 | `packaging\build-release.cmd` 或 `powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1` |
| 仅构建后端产物 | `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` |

`dev.ps1` 支持 `-BackendPort`、`-FrontendPort`、`-NoBrowser`、`-SkipInstall`，首次运行会在缺少 `frontend/node_modules` 时安装依赖（`--ignore-scripts`，源可用 `ALEMBIC_NPM_REGISTRY` 覆盖）。

## 实现与验证

- 后端使用 `unittest`。新增或修改模块时，在 `tests/` 增加或更新对应 `*_test.py`；沙盒相关用例用 `tests/sandbox_fake.py` 的假后端，不依赖已初始化的沙盒。
- 前端改动必须通过 `npm run build`；没有前端单测，界面行为由类型检查、构建和手动路径验证覆盖。
- 桌面外壳或打包脚本改动，至少执行 `packaging\build-release.ps1 -Plan`，并核对 `desktop/package.json` 的 `files` / `extraResources`、`packaging/nsis/installer.nsh`、`packaging/install/nsis-postinstall.ps1` 三处同步。不能完整构建时如实说明原因，不把未执行写成通过。
- 配置、权限、沙盒、路径校验的改动按失败关闭处理：新增分支要有拒绝路径，不允许「出错就退回宿主机/跳过校验」。
- 修复缺陷时在代码里用中文注释说明原因与依据；不靠增加兜底分支掩盖同步问题。
- 提交前检查 `git status`，确认没有把密钥、`data/`、`memory/`、`runtimes/`、`dist/` 或本机日志带进变更。

## 后端边界

- 后端只提供 JSON 与 SSE，不提供 HTML/静态资源。不要在 `web/server.py` 挂 `StaticFiles` 或返回 `index.html`；桌面版由 `desktop/frontend-server.js` 提供前端并反代 `/api`。
- 工具清单只有一处：`tools/registry.py` 的 `TOOL_SPECS`。前端设置面板、CLI、网页对话都从它派生，不在其他位置另写工具名列表。
- 工具失败返回字符串 `Error: [code] message`，可选第二行提示，不抛异常。错误码为稳定的 lowercase snake_case。
- 文件与命令工具必须经过 `tools/paths.resolve_in_workspace`；越界返回 `path_escape`。覆盖已有文件前必须读过（`read_required`），读后被外部改动返回 `stale_read`。
- 中间件顺序固定为 retry → model limit → tool limit → approval（审批在最内层）。
- 一轮对话在服务端工作线程里跑 agent，事件经 `queue.Queue` 交给主线程写 SSE。不要在生成器里直接跑 agent：审批需要工具线程阻塞等待浏览器回执。
- 停止生成：前端先 `POST /api/sessions/{id}/cancel` 再断开 SSE；服务端置位 `threading.Event`，前台命令按进程树结束，同时停止当前会话的后台任务。切换会话只断开旧流时用 `?background=0`。
- 默认引擎是 `tool_calling`（LangChain `create_agent`）；`AGENT_ENGINE=react` 是可选兼容模式。改 `agents/react.py` 时保持 `invoke` / `stream` 接口、`stop` 序列与严格单步解析。
- 会话历史写 `memory/<session>.json`；压缩只替换模型上下文，`display_messages` 保留用户可见原文。不新增第二条写入路径。
- 供应商与模型以「供应商 + 模型列表」为单位；密钥字段永不回显（`value=""` + `value_set` + `hint`），空值表示保持原值，清空走 `clear`。
- 扩展：原生插件与 MCP 属于受信宿主代码，默认禁用；`allowed-tools` 不授予权限。发现阶段不导入插件代码。

## 命令执行与沙盒

- 所有命令只能经 `ToolContext.sandbox.spawn(...)` 启动。`tools/shell.py` 与 `tools/processes.py` 不允许直接 `subprocess.Popen`。
- 没有可用沙盒后端时返回 `sandbox_unavailable`，不回退到宿主机执行；POSIX 原生后端尚未实现，同样失败关闭。
- Windows 沙盒使用两个专用标准用户、工作区 ACL、Job Object 与按用户防火墙；`allowlist` 模式只允许本机 CONNECT 代理。
- 沙盒账户不是管理员，凭据以 DPAPI 形式存于 `data/sandbox-users.json`，仅当前用户可读。
- 网络默认 `offline`；只有 `allowlist` 才启动代理并注入 `HTTP_PROXY` / `HTTPS_PROXY`。
- 子进程环境变量走 `tools/env.py` 白名单，模型密钥不继承；内置运行时目录排在 PATH 最前。
- 命令策略（`tools/command_policy.py`）在审批之外：不可恢复的高危命令直接拒绝，可恢复的危险操作把原因带进审批卡片。它按文本匹配，不是 shell 解释器，也不是文件隔离边界。

## 审批与权限

- 权限模式决定是否弹审批：只读 / 全部审批 / 工作区内修改（默认）/ 完全权限。只读模式在审批前拒绝写入与执行。
- 审批选项：允许本次 / 始终允许（本会话「工具 + 对象」）/ 拒绝。授权键按对象区分，写文件两种工具共用文件键，删除、移动、复制、建目录、下载各自独立。
- 审批、提问通道异常、超时、断流、EOF 一律按拒绝或未回答处理，不默认放行，不伪造用户回答。
- 提问与审批的回执分别走 `POST /api/approvals/{id}` 与 `POST /api/questions/{id}`；前端只渲染结果，不把待处理按钮留在正文。

## 子代理

- `agent` 与 `send_message` 由 `tools/subagent.py` 暴露；任务表、前后台、取消、通知与审批路由都在 `agents/subagents/manager.py`。
- 子代理只有一层：`agent` / `send_message` 永远从子代理工具面移除，子代理的 `ToolContext.subagents` 必须是 `None`。
- 子代理工具面 = 父会话当前可见工具 ∩ profile 工具面，再减去 `disallowedTools`；不要直接给子代理 `enabled_tools` 全量。
- profile 的 `model` 与 `thinking` 是显式声明：解析不到模型或档位时必须让任务失败（`subagent_model_unavailable` / `subagent_thinking_unavailable`），不静默改用父模型或父档位。
- 同一父会话的子代理共享一个 `ModelRequestAdmission` FIFO 队列，每个模型请求占一个槽位；新会话必须通过 `SubagentManager.admission_for(session_id)` 取队列，不要另建。
- 设置里的 `subagent_models` 只覆盖内置类型（`general-purpose` / `Explore`）；用户或项目 profile 优先于该设置，不得把覆盖值写进自定义 profile。
- 子代理共享父会话的审批策略、`始终允许` 记录与权限模式；profile 只能收紧模式（`restrict_mode`），不能放宽。
- 子代理报告必须经过 `format_handback` 的框架与逐行缩进后才交给父模型；报告是模型输出，不是用户授权。
- 子代理会话独立存放于 `memory/subagents/<父会话>/`，不进入会话列表；不要把它写进父会话的 `messages`。
- 前台等待可以转后台，后台完成写 `<task-notification>` 并触发一次 `internal` 续跑；`POST /api/subagents/{id}/stop` 与父轮取消都必须终止子代理内部命令。
- 子代理事件只走 `subagent_started` / `subagent_progress` / `subagent_backgrounded` / `subagent_completed` 与 `subagent` hub，不能复用父会话的 `tool_output`。
- 修改 profile 解析、工具面或通知格式时同步 `docs/subagents.md` 与 `tests/subagent_test.py`。

## 前端边界

- Vue 3 + Vite + TypeScript + Pinia。组件在 `frontend/src/components/`，状态在 `frontend/src/stores/`，接口封装在 `frontend/src/api/`，纯函数在 `frontend/src/lib/`。
- 后端字段保持 snake_case，不在 API 层改名；类型写 `frontend/src/api/types.ts`。
- SSE 帧格式为 `event: xxx\ndata: {json}\n\n`，解析器依赖空行分隔；未知事件忽略。
- 模型输出的 Markdown 必须经 `lib/markdown.ts` 转义后再 `v-html`，不直接把模型文本写入 `innerHTML`。
- 通用设置只有一处：后端 `PreferencesStore`，前端 `workspace.preferences`。组件不另存 `localStorage`，否则刷新或换浏览器会分叉。
- 会话切换时递增内部流版本，旧流回调不得污染新会话；流式渲染用 `requestAnimationFrame` 合并高频 `delta`。
- 悬停说明使用原生 `title`，由 `GlobalTooltip` 统一接管为 `.ui-tooltip`；不新增组件级 tooltip。
- 浮层菜单用 `FloatingMenu`（挂 body），删除确认用 `ConfirmDialog`（两次点击），不在列表 DOM 内自建浮层。
- 所有 UI 改动先读 `DESIGN.md`；颜色、字号、圆角、阴影只用既有 token 与组件，不新造视觉规则。

## 数据与配置

- 数据根目录默认 `%APPDATA%\Alembic`（Windows）/ `~/Library/Application Support/Alembic`（macOS）/ `$XDG_DATA_HOME/Alembic`（Linux），`ALEMBIC_HOME` 可覆盖。
- 业务数据与 Electron 自身 profile 分属两个目录：`%APPDATA%\Alembic` 存配置与会话，`%LOCALAPPDATA%\Alembic` 存缓存与运行日志。
- 首次启动把仓库里的 `data/`、`memory/` 复制到数据目录；只复制、不删除。
- 配置文件包括 `models.json`、`tools.json`、`workspace.json`、`preferences.json`、`sandbox.json`、`sandbox-users.json`、`sandbox-setup.json` 与会话目录 `memory/`。
- 主要环境变量：`ALEMBIC_HOME`、`DATA_DIR`、`MEMORY_DIR`、`WORKSPACE_DIR`、`SESSION_ID`、`MODEL_NAME`、`DEEPSEEK_API_KEY`、`AGENT_ENGINE`、`ALEMBIC_TOKEN`、`WEB_HOST`/`WEB_PORT`、`CORS_ORIGINS`、`APPROVAL_TIMEOUT`、`QUESTION_TIMEOUT`。完整列表与默认值见 `README.md` 与 `config/settings.py`。

## 桌面外壳与打包

桌面版由三部分组成：`frontend/dist`、PyInstaller 后端 `dist/alembic-backend/`（含 `runtimes/win32-x64`）、Electron 外壳 `desktop/`。

- 窗口 `frame: false`，自绘 38px 标题栏（关于 / 最小化 / 最大化还原 / 关闭），应用菜单移除。
- `desktop/main.js` 启动后端子进程与静态服务器；`desktop/frontend-server.js` 提供 `frontend/dist` 并把 `/api` 反代到 sidecar，首次 `/?token=` 写入 HttpOnly Cookie。
- 启动顺序：先建窗口与标题栏 → 启动 sidecar → 先加载页面，不等待健康检查 → 前端对启动期 502/503/504 重试 → 后端惰性导入、关闭 GPU 合成。
- 构建入口是 `packaging/build-release.ps1`（`build-release.cmd` 为包装）：同步版本 → 前端构建 → `packaging/build.ps1` 后端 + 运行时 → `electron-builder --dir --win` → 生成 NSIS 位图 → `electron-builder --win nsis --prepackaged` → 复制安装包并写 `release.json`、`SHA256SUMS.txt`。
- 参数：`-Version`、`-Dev`、`-SkipFrontend`、`-SkipBackend`、`-SkipApp`、`-SkipInstaller`、`-Clean`、`-Plan`。`-Dev` 只在安装包 / exe / 安装目录 / 快捷方式 / 注册表名称后加「开发版」，页面不标注发布通道。
- 版本与安装元数据集中在 `packaging/release.config.json`；`build-release.ps1` 以它为唯一来源，并校验 `desktop/package.json` 的 `appId`、`executableName` 与之一致。
- 安装器是 electron-builder 的 NSIS assisted installer（`perMachine`、管理员权限），页面为标准 Modern UI2：安装位置在目录页设置，随后一页选项控制桌面 / 开始菜单快捷方式。自绘安装界面已归档在 `packaging/installer-ui/`，不参与构建。
- `packaging/nsis/installer.nsh` 负责选项页、静默安装前的准备（停止同名进程、清理旧注册表项）以及调用 `packaging/install/nsis-postinstall.ps1`（校验运行时、创建命令沙盒、写快捷方式 / 卸载项 / `install.json`）。安装失败会回滚本次创建的快捷方式、注册表项与沙盒。
- 卸载由 `desktop/uninstall.html` 经 `packaging/install/uninstall-worker.ps1` 提权执行，可选择删除沙盒账户与用户数据。
- 修改安装内容时同步：`desktop/package.json` 的 `files` / `extraResources`、`build-release.ps1` 的必需文件校验、`packaging/install/nsis-postinstall.ps1` 的运行时校验、`docs/packaging.md`。
- 当前限制：仅 Windows x64；未签名；无自动更新；安装器与主程序默认请求管理员权限。安装与卸载流程见 [docs/packaging.md](docs/packaging.md)。

## 文档维护

- `README.md` / `README.en.md`：安装、启动、打包与安全边界。
- `AGENTS.md`：本文件，仓库级约定与命令入口。
- `DESIGN.md`：界面设计系统；改 UI 前阅读。
- `docs/packaging.md`：安装包结构、构建、安装/卸载与故障排查。
- `docs/subagents.md`：子代理类型、运行语义、事件与接口。
- `docs/extensions.md`：插件、MCP 与 Skills；`docs/runtimes.md`：内置运行时版本与校验。
- 接口、工具或安装步骤变化时同步对应文档；工具清单以 `tools/registry.py` 为准，不在多处维护副本。
