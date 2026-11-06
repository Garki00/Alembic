@echo off
rem 启动器：某些受限环境里 Chromium 的 GPU/渲染子进程会被系统拒绝创建
rem （日志里是 launch-failed / systemErrorCode=5），此时必须关掉 Chromium 沙箱。
rem 双击本文件即可，参数已经写死。
cd /d "%~dp0"
start "" "%~dp0Smart Agent.exe" --no-sandbox --disable-gpu
