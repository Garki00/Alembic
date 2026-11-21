# ALEMBIC 桌面外壳

Electron 只负责窗口、进程和本地资源；智能体本身是独立的 Python sidecar。
前端是 `frontend/` 的 Vue 构建产物，由外壳内的静态服务器提供。

完整打包、NSIS 安装器和发布流程见 [docs/packaging.md](../docs/packaging.md)。

## 开发运行

```powershell
cd frontend
npm install
npm run build

cd ..\desktop
npm install
npm start          # 启动后端 + 静态前端 + 窗口
npm run smoke      # 无窗口自检，结果写入 ALEMBIC_SMOKE_REPORT
```

`npm start` 默认使用：

- 后端：仓库 `.venv` 的 `python -m web`；
- 前端：`frontend/dist`，可用 `ALEMBIC_FRONTEND` 指向别的目录；
- 数据：`%APPDATA%\Alembic`（可用 `ALEMBIC_HOME` 覆盖）；
- Electron profile：`%LOCALAPPDATA%\Alembic`；
- 管理员权限：打包后的主程序带 `requireAdministrator` manifest；开发时直接运行
  `electron .` 不要求提权。

想验证打包后的后端：

```powershell
npm run build:backend
npm run start:frozen
```

## 打包

完整的 NSIS 安装包由 `packaging\build-release.ps1` 生成：

```powershell
powershell -ExecutionPolicy Bypass -File ..\packaging\build-release.ps1
# 开发版
powershell -ExecutionPolicy Bypass -File ..\packaging\build-release.ps1 -Dev
```

如果只调试外壳目录：

```powershell
npm run build          # = build:backend + build:app
npm run build:nsis     # prebuild:nsis 会生成 desktop/build/nsis 位图
```

- `build:app` 生成 `desktop\dist\win-unpacked\ALEMBIC.exe`；
- `build:nsis` 使用 `--prepackaged` 打包上面的目录，生成
  `desktop\dist\ALEMBIC-Setup-<version>-x64.exe`；
- 正常发布不要绕过 `build-release.ps1`，它负责前端、后端、版本、位图和 `开发版` 命名。

产物：

```text
build\release\out\ALEMBIC-Setup-<version>-x64.exe
build\release\out\SHA256SUMS.txt
```

## 启动时做了什么

1. 创建自定义标题栏窗口（`frame: false`，无 ALEMBIC 文案）；
2. 生成随机 token，设 `ALEMBIC_TOKEN`、`ALEMBIC_PORT=0` 拉起后端；
3. 后端把 `{"port": ..., "url": ...}` 写到 `<userData>/runtime/sidecar.json`；
4. 外壳启动前端静态服务器（`frontend-server.js`）并**立即加载页面**；
5. `/api/bootstrap` 在启动期对 502/503/504 重试，后端就绪后填充工作区；
6. 后端健康检查失败时，窗口替换为错误页；
7. 退出时 `taskkill /T` 结束后端进程树，并关闭静态服务器。

窗口控制通过 `desktop/preload.js` 暴露的最小 IPC：

- `app:info`：名称、版本；
- `window:minimize` / `window:toggle-maximize` / `window:close`；
- `window:maximized` 事件用于切换最大化/还原图标。

标题栏「关于」打开居中窗口，显示名称与版本；点击遮罩或按 Esc 关闭。应用菜单被移除，
窗口内没有开发者工具入口。

## 卸载模式

安装目录里的 `ALEMBIC-Uninstall.exe` 是主程序副本。它通过文件名或
`--uninstall` 进入卸载界面（`uninstall.html`），不启动后端：

```powershell
npm run start:uninstall
```

开发时可用 `ALEMBIC_UNINSTALL_DIR` 指向一个放了假 `install.json` 的目录来调试界面
（真正卸载仍需要管理员权限）。卸载界面会读取安装根目录的 `install.json`，再通过
UAC 调用 `resources\install\uninstall-worker.ps1`。详见
[docs/packaging.md](../docs/packaging.md)。

## 日志与排障

- 外壳日志：`%LOCALAPPDATA%\Alembic\runtime\desktop.log`；
- 最早启动标记：同目录 `boot.log`；
- 后端日志：`%LOCALAPPDATA%\Alembic\runtime\backend.log`；
- 启动页会先显示「正在启动智能体后端…」，失败时在窗口里显示原因和日志路径。

常见问题：

- **窗口空白 / GPU 子进程 `launch-failed`**：外壳启动时关闭 GPU 合成；仍失败时会带
  `--no-sandbox --disable-gpu` 重启一次；也可设 `ALEMBIC_FORCE_NO_SANDBOX=1`。
- **后端起不来**：先看 `backend.log`；开发版可用 `npm run start:frozen` 排除
  `.venv` 问题，打包版检查 `resources\backend\alembic-backend.exe`。
- **构建时卡在下载**：

  ```powershell
  $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
  $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
  ```

- **受限环境里 Electron 起不来**：先 `Remove-Item Env:ELECTRON_RUN_AS_NODE`，
  必要时从命令行加 `--no-sandbox`（脚本内设置太晚）。

## 数据在哪

后端默认把配置和会话放在 `%APPDATA%\Alembic\`；Electron 自己的缓存和日志在
`%LOCALAPPDATA%\Alembic\`。安装版卸载时可选择删除这两处，工作区目录不受影响。
