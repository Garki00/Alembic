@echo off
rem 前后端分离后，打包出来的后端只提供 /api，不再返回网页。
rem 这个文件保留下来是为了提醒以后适配 Electron / 打包流程时不要直接打开 8000。
echo ============================================
echo   Smart Agent 目前请用前后端分离方式启动
echo ============================================
echo.
echo 开发环境：
echo   1. 仓库根目录运行 uv sync
echo   2. 运行 scripts\dev.cmd（或 scripts\dev.ps1）
echo   3. 浏览器打开 http://127.0.0.1:5173
echo.
echo 后端 API：http://127.0.0.1:8000/api
echo 前端构建：cd frontend ^&^& npm run build
echo.
pause
