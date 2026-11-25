# ALEMBIC

[English](README.en.md)

ALEMBIC 是一个本地智能体工作区。它把模型、工具、审批、工作区和会话集中在一个浏览器界面中，适合在本机文件和代码上进行可审阅、可持续的工作流。

后端使用 Python、FastAPI 和 LangChain，前端使用 Vue、Vite 和 TypeScript。前后端通过本地 API 与 SSE 通信，应用数据默认保存在当前用户目录。

## 特性

- 支持多个供应商和模型，可分别配置上下文长度、最大输出与推理等级。
- 通过模型原生工具调用驱动文件读取、编辑、搜索、Git、命令和后台任务。
- 子代理：把独立的多步任务交给带独立上下文与工具面的子代理，支持前台等待、后台完成通知与继续对话。
- 支持文档、图片、网页内容和附件处理。
- 会话持久化、上下文压缩、任务清单、流式输出和后台任务通知。
- 工具审批、计划 / 构建 / 编辑 / 免审批四档权限模式、权限规则与工具能力矩阵、命令策略和凭据路径保护。
- 支持工具插件、MCP 服务器与 Agent Skills。
- 提供浏览器工作区、命令行入口和 Windows 桌面安装包。

## 快速开始

### 环境

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm
- Windows 10 1809+ x64（使用原生命令沙盒时）

### 安装与启动

```powershell
uv sync
cd frontend
npm install
cd ..

# Windows 命令沙盒，仅需以管理员身份执行一次
.\scripts\setup-sandbox.ps1

# 启动后端和前端，并打开浏览器
.\scripts\dev.ps1
```

默认地址：`http://127.0.0.1:5173`。

也可以分别启动：

```powershell
# 终端 1
.\.venv\Scripts\python.exe -m web

# 终端 2
cd frontend
npm run dev
```

不使用命令工具时，可以跳过沙盒初始化；需要执行命令时，应完成初始化并在设置中确认审批与网络策略。

### 桌面安装包

```powershell
# 一次性安装构建依赖
uv sync
cd frontend;  npm install; cd ..
cd desktop;   npm install; cd ..

# 构建 NSIS 安装包
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1

# 开发版（exe/安装包名称带“开发版”，页面不显示版本通道）
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1 -Dev
```

产物在 `build\release\out\ALEMBIC-Setup-<version>-x64.exe`。安装器是原生 NSIS，
使用标准 Modern UI2 向导：安装位置在目录页修改，随后一页选项控制两个快捷方式；
运行时与 Windows 命令沙盒始终安装。安装器和主程序都默认请求管理员权限。
版本号、安装流程、开发版命名和代码签名说明见
[桌面安装包与发布流程](docs/packaging.md)。

### 命令行

```powershell
.\.venv\Scripts\python.exe main.py "检查当前工作区"
```

命令行模型至少需要配置 `DEEPSEEK_API_KEY` 和 `MODEL_NAME`。浏览器端可在初始化向导或设置中配置供应商。

## 使用方式

1. 选择或添加一个工作区。
2. 在设置中配置模型，并按需要启用工具。
3. 在对话中描述目标；需要修改文件或执行命令时，应用会按当前策略请求审批。
4. 通过工作步骤、实时输出和文件 Diff 检查结果。

## 安全边界

- API 默认只监听本机回环地址。对外提供服务前，必须配置访问令牌、限制监听地址和 CORS 来源，并使用受保护的传输层。
- 不要把 API Key、MCP 凭据或其他秘密提交到 Git；应用配置和会话文件应按敏感数据保护。
- 工具调用受审批、工具开关和命令策略共同约束。原生插件与 MCP 服务器属于受信任扩展，应只安装和启用已审查的来源。
- Windows 命令沙盒通过专用本地用户、工作区 ACL、进程树控制和网络策略限制命令。没有可用沙盒时，命令执行会失败关闭。
- 模型输出、网页内容、插件描述和 MCP 返回值都属于不可信数据，不能替代用户授权或安全审查。

## 开发

```powershell
# 后端测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*_test.py"

# 前端类型检查与生产构建
cd frontend
npm run build
```

扩展开发、子代理、可移植运行时、仓库约定和界面规范见：

- [扩展指南](docs/extensions.md)
- [子代理](docs/subagents.md)
- [内置运行时](docs/runtimes.md)
- [仓库约定](AGENTS.md)
- [界面设计系统](DESIGN.md)

## 配置与数据

默认数据目录：

- Windows：`%APPDATA%\Alembic\`
- macOS：`~/Library/Application Support/Alembic/`
- Linux：`$XDG_DATA_HOME/Alembic/`，未设置时为 `~/.local/share/Alembic/`

可使用 `ALEMBIC_HOME` 覆盖数据根目录。主要内容包括模型与工具配置、工作区列表、会话历史、任务清单和后台进程日志。

## 许可证

本项目使用 [MIT License](LICENSE)。
