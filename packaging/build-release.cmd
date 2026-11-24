@echo off
rem 双击或从任意目录调用：构建 ALEMBIC 单文件安装包。
rem 参数原样传给 build-release.ps1，例如：
rem   packaging\build-release.cmd -Version 0.2.0
rem   packaging\build-release.cmd -SkipFrontend -SkipBackend
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-release.ps1" %*
exit /b %errorlevel%
