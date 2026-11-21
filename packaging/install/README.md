# 安装 / 卸载脚本

这两个脚本由 NSIS 安装器或 Electron 卸载界面以管理员权限调用，**不要手动在普通权限下运行**。

| 文件 | 调用方 | 作用 |
| --- | --- | --- |
| `nsis-postinstall.ps1` | `packaging/nsis/installer.nsh` | 校验运行时、创建命令沙盒、按选项建快捷方式、复制卸载器、写注册表与 `install.json` |
| `uninstall-worker.ps1` | `desktop/main.js`（卸载模式） | 停止应用、删快捷方式 / 注册表、可选删沙盒与用户数据、安排删除安装目录 |

NSIS 本身负责解压应用文件；`nsis-postinstall.ps1` 只做需要 Windows API 的收尾工作。
安装目录、快捷方式、沙盒和注册表在同一个失败回滚范围内。

## 安装参数

`nsis-postinstall.ps1` 由 `installer.nsh` 传入：

```text
-InstallDir                 NSIS 的 $INSTDIR
-AppExe                     主程序文件名（开发版为 ALEMBIC-开发版.exe）
-Version                    版本号
-Publisher                  发布者
-DesktopShortcut            1 / 0
-StartMenuShortcut          1 / 0
-LogFile                    %TEMP%\alembic-install.log
```

运行时和 Windows 命令沙盒没有开关，始终安装。

## 退出码

- `0` 成功；`1` 失败。
- 失败时输出 `ERROR: ...`，NSIS 显示消息并删除不完整的安装目录。
- 权限不足、运行时缺失、沙盒创建失败、注册表写入失败都会失败退出。

`install.json` 是卸载界面的唯一数据来源，包含：

- `install_dir` / `app_exe` / `uninstaller`；
- `app_home`、`paths.local_app_data`；
- `options`（两个快捷方式、沙盒）；
- `sandbox.created`；
- `shortcuts`；
- `registry.uninstall_key` / `registry.app_path_key`。

## 进度

NSIS 安装界面使用原生进度页，postinstall 的输出写入安装日志；不再使用 JSONL 进度协议。
卸载界面仍通过 `uninstall-worker.ps1` 的 JSONL 进度协议更新步骤。

## 安全约束

- 安装器和卸载器都以管理员权限运行；NSIS 是 `perMachine`，主程序带
  `requireAdministrator` manifest。
- 沙盒凭据是 DPAPI `CurrentUser` 加密，所以安装时确认 UAC 的账户应与之后运行
  ALEMBIC 的账户一致；否则应用无法解密沙盒凭据。
- 失败时回滚本次创建的快捷方式、注册表项和沙盒；用户数据不在回滚范围内。
- 不要在这些脚本里直接执行模型提供的命令或拼接未校验的路径。
