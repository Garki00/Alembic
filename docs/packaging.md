# 桌面安装包与发布流程

ALEMBIC 桌面版由三部分组成：

1. **前端**：`frontend/dist`（Vue + Vite 生产构建）；
2. **后端 sidecar**：`dist/alembic-backend/`（PyInstaller onedir，内置 Node / Python / pnpm）；
3. **Electron 外壳**：`desktop/`（窗口、静态前端、`/api` 反向代理、sidecar 生命周期）。

安装器使用 **原生 NSIS**，提供与前端一致的纸色 / 祖母绿界面。安装器和主程序都要求管理员权限。

## 1. 产物

```text
build/release/out/
  ALEMBIC-Setup-<version>-x64.exe           发行版安装包
  ALEMBIC-开发版-Setup-<version>-x64.exe    开发版安装包
  SHA256SUMS.txt
  release.json
```

开发版只在**安装包、安装后的 exe 名称、安装目录、快捷方式和注册表名称**后加
`开发版`；应用页面不显示“发行版 / 开发版”标记。

安装后目录（默认 `C:\Program Files\ALEMBIC`）：

```text
ALEMBIC.exe                  主程序（requireAdministrator）
ALEMBIC-Uninstall.exe        卸载程序（安装时复制）
install.json                 安装记录
LICENSE
启动 ALEMBIC（兼容模式）.cmd
locales\ / *.dll / *.pak      Electron/Chromium 运行时
resources\app.asar           Electron 外壳
resources\frontend\          Vue 构建产物
resources\backend\           PyInstaller 后端 + runtimes/win32-x64
resources\install\           setup-sandbox.ps1 / nsis-postinstall.ps1 / uninstall-worker.ps1
```

## 2. 构建准备

要求：Windows 10 1809+ x64、Node.js 20+、Python 3.11+、[uv](https://docs.astral.sh/uv/)，
以及 **Visual Studio 的「使用 C++ 的桌面开发」工作负载（含 x86/x64 MSVC 工具链）**。
安装界面是 NSIS 进程内的原生 Win32/GDI+ 插件，构建时需要 `cl.exe` 编译 x86 DLL；
`make-nsis-assets.ps1` 会在缺少工具链时直接报错，不会退回到默认 NSIS 界面。

```powershell
uv sync
cd frontend; npm install; cd ..
cd desktop;  npm install; cd ..
```

`desktop` 首次构建会下载 Electron；受限网络可设置：

```powershell
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
$env:npm_config_cache = "$PWD\.tmp\npm-cache"
```

后端通过 `scripts/provision_runtimes.py` 下载锁定版本的 Node / Python / pnpm，缓存在
`.tmp/runtime-downloads`，版本与哈希见 `packaging/runtimes.lock.json` 和
[runtimes.md](runtimes.md)。

## 3. 一条命令构建

```powershell
packaging\build-release.cmd
# 或
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

流程：

```text
同步版本
  → frontend: npm run build
  → backend:  packaging\build.ps1
  → desktop:  electron-builder --dir
  → 生成 NSIS 位图
  → desktop:  electron-builder --win nsis --prepackaged
  → build\release\out\ALEMBIC-Setup-<version>-x64.exe
```

### 参数

| 参数 | 作用 |
| --- | --- |
| `-Version 0.2.0` | 覆盖 `release.config.json` 的版本，并同步 `frontend` / `desktop` 的 `package.json`、`package-lock.json` |
| `-Dev` | 开发版：exe/安装包/安装目录/快捷方式后加 `开发版` |
| `-SkipFrontend` | 复用已有 `frontend/dist` |
| `-SkipBackend` | 复用已有 `dist/alembic-backend` |
| `-SkipApp` | 复用已有 `desktop/dist/win-unpacked` |
| `-SkipInstaller` | 只生成应用目录，不生成 NSIS 安装包 |
| `-Clean` | 删除 `desktop/dist`、`build/release/out`、NSIS 位图和后端构建缓存 |
| `-Plan` | 只打印步骤，不写文件 |

开发版示例：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1 -Dev
```

## 4. NSIS 安装流程

安装器使用 electron-builder 的 NSIS assisted installer，`perMachine: true`，启动时请求管理员权限。

### 界面

安装界面不是默认的 NSIS 向导：NSIS 进程内加载一个自绘的原生 Win32/GDI+ 窗口
（`packaging/installer-ui/alembic-installer-ui.cpp`，x86 Unicode DLL），由它负责整个
可见向导，NSIS 主窗口在自绘页显示期间隐藏。

- 无边框窗口，纸色 / 祖母绿配色，与前端一致，跟随系统浅色 / 深色主题；
- 欢迎页：安装位置输入框、`浏览…`（系统文件夹选择器）、`创建桌面快捷方式`、
  `创建开始菜单快捷方式`、`安装` 按钮，以及最小化 / 关闭；
- 安装页：真实进度（读取 NSIS 进度条消息）；拿不到可验证进度时使用不确定动画，
  不伪造百分比；
- 完成页：`完成` 按钮，关闭安装器；
- `Esc` / 关闭按钮 = 取消，`Enter` = 下一步。

页面的路径与两个快捷方式选项通过 `$PLUGINSDIR\alembic-ui.ini`（UTF-16LE）交回 NSIS，
路径校验（绝对路径、拒绝盘符根目录 / `Program Files` / `Windows` / `System32`、
拒绝已有的非 ALEMBIC 目录）在
[packaging/nsis/installer.nsh](../packaging/nsis/installer.nsh) 的页面 leave 函数里完成。

插件由
[packaging/tools/make-nsis-assets.ps1](../packaging/tools/make-nsis-assets.ps1)
在 `desktop/build/nsis/x86-unicode/` 编译产出，同一脚本仍生成 MUI 位图。

### 安装阶段

NSIS 负责解压应用文件；随后调用
[packaging/install/nsis-postinstall.ps1](../packaging/install/nsis-postinstall.ps1)：

1. 校验内置 Node / Python / pnpm 可执行；
2. 运行 `resources\install\setup-sandbox.ps1`，创建离线 / 允许列表两个本地账户和防火墙规则；
3. 按选项创建公共桌面 / 开始菜单快捷方式；
4. 复制出 `ALEMBIC-Uninstall.exe`；
5. 写 `HKLM` 卸载项和 `App Paths`；
6. 写 `install.json`。

失败时脚本回滚本次创建的快捷方式、注册表项和沙盒；NSIS 删除不完整的安装目录。

安装器**不会**调用旧 NSIS 卸载器：升级时先停止旧应用、清空旧安装目录，再写入新版本。

### 管理员权限

- 安装器：NSIS `perMachine`，`RequestExecutionLevel admin`；
- 主程序：electron-builder `win.requestedExecutionLevel: requireAdministrator`；
- 安装后的 `ALEMBIC-Uninstall.exe` 是主程序副本，因此也以管理员权限启动。

## 5. 卸载

入口：

1. 开始菜单 → `ALEMBIC` → `卸载 ALEMBIC`；
2. Windows 设置 → 应用 → ALEMBIC → 卸载；
3. 安装目录里的 `ALEMBIC-Uninstall.exe --uninstall`。

卸载界面仍是 Electron 页面（`desktop/uninstall.html`），读取 `install.json` 后通过 UAC
调用 `packaging/install/uninstall-worker.ps1`。可选项：

- 删除命令沙盒账户与防火墙规则（仅当 `install.json` 记录沙盒由 ALEMBIC 创建时默认勾选）；
- 删除用户数据（默认不勾选；工作区目录不受影响）。

卸载完成后，清理进程等待界面进程退出再删除安装目录。

## 6. 主程序窗口

- 主窗口使用 `frame: false`，自定义标题栏；
- 标题栏不显示 ALEMBIC，只显示「关于」和最小化 / 最大化 / 还原 / 关闭；
- 点击「关于」在页面中央显示软件名称和版本；点击窗口外关闭；
- 应用菜单被移除，不再显示“开发者工具”入口。

## 7. 启动优化

主程序启动顺序：

1. 创建窗口和标题栏；
2. 启动 Python sidecar 并取得端口；
3. **先启动前端静态服务器并加载页面**，不等待后端健康检查；
4. 前端 `/api/bootstrap` 对 502/503/504 做启动期重试，后端就绪后填充数据；
5. 后端侧 `web.runtime` 采用惰性导入，避免启动时加载 LangChain 运行时；
6. 启动时关闭 GPU 合成，减少受限环境里的 Chromium GPU 子进程失败。

这些改动不改变功能；后端失败时仍会显示错误页。

## 8. 开发版与发行版

`-Dev` 只改变命名，不改变页面：

| 项目 | 发行版 | 开发版 |
| --- | --- | --- |
| 安装包 | `ALEMBIC-Setup-...` | `ALEMBIC-开发版-Setup-...` |
| exe | `ALEMBIC.exe` | `ALEMBIC-开发版.exe` |
| 安装目录 | `Program Files\ALEMBIC` | `Program Files\ALEMBIC-开发版` |
| 快捷方式 / 注册表名称 | `ALEMBIC` | `ALEMBIC-开发版` |

关于窗口始终显示 `ALEMBIC` 和版本号，不显示发布通道。

## 9. 扩展点

### 修改安装选项

- 页面控件和文案：`packaging/installer-ui/alembic-installer-ui.cpp`
- 选项回传与路径校验：`packaging/nsis/installer.nsh`
- 选项传入 postinstall 的参数：`customInstall` 中的 PowerShell 命令行
- postinstall 行为：`packaging/install/nsis-postinstall.ps1`

### 修改安装内容

`desktop/package.json` 的 `extraResources` 决定随应用安装的内容。新增内容后同步更新：

- `build-release.ps1` 的必需文件校验；
- `nsis-postinstall.ps1` 的运行时/资源校验（如适用）。

### 修改品牌

- 安装器位图：`packaging/tools/make-nsis-assets.ps1`
- 安装器页面配色 / 字体 / 控件位置：`packaging/installer-ui/alembic-installer-ui.cpp`
- 安装器页面流程与校验：`packaging/nsis/installer.nsh`
- 应用图标：`packaging/assets/alembic.svg`
- 前端标题栏/关于窗口：`frontend/src/components/AppTitlebar.vue`、
  `frontend/src/components/AboutDialog.vue`

### 代码签名

当前未签名，SmartScreen 会显示未知发布者。准备好证书后：

```powershell
$env:CSC_LINK = "C:\keys\alembic.pfx"
$env:CSC_KEY_PASSWORD = "..."
powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1
```

electron-builder 会签名应用 exe 和 NSIS 安装包。

## 10. 故障排查

| 现象 | 处理 |
| --- | --- |
| NSIS 位图或原生界面 DLL 缺失 | 使用 `build-release.ps1` 构建，不要直接调用 `electron-builder --win nsis` |
| 构建报 “Visual C++ x86/x64 build tools are required” | 安装 Visual Studio「使用 C++ 的桌面开发」工作负载（含 x86 工具链）后重试 |
| Electron 下载超时 | 设置 `ELECTRON_MIRROR`，或重试 |
| NSIS 编译警告被当作错误 | 检查 `packaging/nsis/installer.nsh`；构建启用了 `warningsAsErrors` |
| 安装时提示无法写入 Program Files | 确认 UAC 已允许；安装器默认请求管理员 |
| 安装后启动没有页面 | 查看 `%LOCALAPPDATA%\Alembic\runtime\desktop.log`；确认不是被 SmartScreen 拦截 |
| SmartScreen 未知发布者 | 配置代码签名；当前安装包未签名 |
| 命令沙盒不可用 | 查看安装日志；确认安装时 UAC 使用的是运行 ALEMBIC 的同一管理员账户 |
| 卸载后目录仍在 | 关闭卸载窗口，几秒后清理进程会删除；仍失败可手动删除 |

日志：

- 安装：`%TEMP%\alembic-install.log`
- 应用外壳：`%LOCALAPPDATA%\Alembic\runtime\desktop.log`、`boot.log`
- 后端：`%LOCALAPPDATA%\Alembic\runtime\backend.log`

## 11. 安全边界与限制

- 安装器和主程序都以管理员运行；每次启动都会弹 UAC，这是当前产品要求。
- 沙盒凭据是 DPAPI `CurrentUser` 加密；安装时使用哪个管理员账户，应用也应以该账户运行。
- 按机器安装到 `Program Files`，卸载需要管理员。
- 当前未签名；正式分发前应配置 EV/OV 代码签名。
- 没有自动更新；升级时运行新安装包，安装器会清理旧目录并保留用户数据。
