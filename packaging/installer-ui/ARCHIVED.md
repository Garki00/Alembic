# 已归档：定制化安装界面（不启用）

此目录是“自绘安装向导”的实现，现已**归档并停用**。

- 安装器改用 electron-builder 自带的 Modern UI2 标准向导：
  `packaging/nsis/installer.nsh`；
- `packaging/tools/make-nsis-assets.ps1` 不再编译这里的 DLL（脚本里已 `return` 提前退出）；
- `desktop/package.json` 不再引用任何插件目录。

保留原因：记录当时的设计与踩过的问题，便于以后需要时重新启用。

## 文件

- `alembic-installer-ui.cpp`：Win32/GDI 绘制 + NSIS 插件 ABI 的实现；
- `alembic-installer-ui.def`：`AlembicCreate / AlembicSetMode / AlembicShutdown` 导出表。

## 若要重新启用

1. 恢复 `make-nsis-assets.ps1` 中被 `return` 跳过的编译段；
2. 在 `installer.nsh` 里重新加入 `!addplugindir` 与 `alembic-installer-ui::AlembicCreate` 调用；
3. 注意已知问题：在提权、刚解压的安装器进程里，自绘窗口曾出现“透明、不可点击、随后消失”，
   排查过程中移除了 GDI+、DWM 调用与同步首绘，仍需在有界面环境下重新验证。
