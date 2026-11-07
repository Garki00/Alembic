# ALEMBIC 桌面外壳

> **注意：Electron 外壳尚未适配 Vue 前端（前后端分离）。**
> 后端现在只提供 `/api`，`desktop/main.js` 仍然打开后端根地址，因此会看到 JSON
> 而不是界面。浏览器开发请用 `scripts\dev.ps1`（前端 5173 + 后端 8000）；
> 等需要恢复桌面版时，再让外壳指向 `frontend/dist` 构建产物或启动 Vite。
> 下面的内容保留为旧版外壳的参考。

Electron 只负责窗口与进程管理，智能体本身是独立的后端进程（sidecar）。

## 运行

```powershell
npm install        # 首次
npm start          # 用仓库里的 .venv 跑 python -m web
npm run smoke      # 无窗口自检
```

开发时也可以让外壳改用打包好的后端：

```powershell
npm run build:backend   # 先构建 dist\alembic-backend
npm run start:frozen    # electron . --frozen
```

解释器找不到时会依次尝试 `ALEMBIC_PYTHON` → `.venv` → PATH 里的 `python`。

## 打包成 exe

```powershell
npm run build      # = build:backend + build:app
```

产物 `desktop\dist\win-unpacked\ALEMBIC.exe`（约 412 MB），整个目录可直接分发：

```
ALEMBIC.exe
resources\app.asar            外壳代码
resources\backend\            打包后的 Python 后端（约 56 MB）
```

启动时的后端优先级：

1. `ALEMBIC_BACKEND` 环境变量指定的可执行文件；
2. `--frozen` 时用仓库里的 `dist\alembic-backend`；
3. 打包应用（`app.isPackaged`）用 `resources\backend\alembic-backend.exe`；
4. 否则用仓库 `.venv` 的 `python -m web`。

## 它做了什么

1. 生成随机 token，设 `ALEMBIC_TOKEN`、`ALEMBIC_PORT=0` 拉起后端；
2. 后端 bind 系统分配的端口，把 `{"port": ..., "url": ...}` 写到
   `<userData>/runtime/sidecar.json`，日志写 `backend.log`（不走管道 stdio，
   受限环境里管道会被拒）；
3. 外壳轮询端口文件 + `/api/health`，就绪后打开窗口，首次访问 `/?token=…`，
   服务端换成 HttpOnly Cookie 并跳回 `/`；
4. 退出时用 `taskkill /T` 结束整棵进程树，不留孤儿进程。

## 自检

```powershell
npm run smoke
```

窗口不显示，加载完成后统计工作区 / 会话 / 工具卡片数量，写入
`ALEMBIC_SMOKE_REPORT` 指定的文件（未设置则写到日志），随后退出。
超过 20 秒未完成会写一条 `{"error":"timeout"}` 并退出，不会挂住。

## 故障排查

- **启动时的界面**：窗口会先显示「正在启动智能体后端…」，就绪后切到应用；后端起不来时
  窗口里直接显示错误原因与日志路径，不会出现「什么都没有」。
- **启动日志**：打包版默认写 `<userData>\runtime\desktop.log`，也可以用
  `ALEMBIC_LOG` 指定；后端自身日志在 `<userData>/runtime/backend.log`。
  日志逐条记录后端 PID、端口、健康检查、页面加载。
- **后端就绪超时**：90 秒。首次运行被杀毒软件扫描大文件时可能偏慢；超时后显示错误页。
- **窗口一片空白 / 只有个空壳**：如果日志里出现

  ```
  子进程退出：{"type":"GPU","reason":"launch-failed","exitCode":18,"systemErrorCode":5}
  渲染进程退出：{"reason":"launch-failed","exitCode":18}
  ```

  说明 Chromium 的 GPU / 渲染子进程被系统拒绝创建（`systemErrorCode=5` 是拒绝访问），
  页面自然渲染不出来。外壳会自动改用 `--no-sandbox --disable-gpu` 重启一次；
  想跳过第一次尝试可以直接设 `ALEMBIC_FORCE_NO_SANDBOX=1`，或从命令行启动：

  ```powershell
  ".\dist\win-unpacked\ALEMBIC.exe" --no-sandbox
  ```

  应用只加载本机的 `http://127.0.0.1` 页面、外链一律交给系统浏览器，因此关闭
  Chromium 自带沙箱的风险可控。
- **闪退（受限环境）**：如果整个主进程都起不来（日志里连 `main.js 已加载` 都没有），
  同样是 Chromium 沙箱初始化失败，用命令行关掉它再试：

  ```powershell
  npx electron . --no-sandbox
  ```

  注意 `app.commandLine.appendSwitch("no-sandbox")` 在脚本里调用太晚，必须放在
  启动参数里。
- **构建时卡在下载**：electron-builder 默认从 GitHub 取运行时，可换镜像：

  ```powershell
  $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
  $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
  ```

## 数据在哪

后端默认把配置和会话放在 `%APPDATA%\Alembic\`（首次启动会把仓库里的 `data/`、
`memory/` 复制过去，原目录保留）。可用 `ALEMBIC_HOME` 指到别处，外壳会把它
传给 sidecar，适合做便携版。

## 还没做

- 安装包（NSIS）与代码签名：当前是免安装目录，未签名，SmartScreen 首次运行会提示；
- 应用图标（现在用的是 Electron 默认图标）。
