@echo off
rem 一键启动本地开发环境（后端 8000 + 前端 5173）。
rem 用法：scripts\dev.cmd [--NoBrowser] [--BackendPort 8001] [--FrontendPort 5174]
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
