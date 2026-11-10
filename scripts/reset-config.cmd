@echo off
chcp 65001 >nul
rem Reset Alembic / SmartAgent config to show the first-run setup again.
rem Usage: scripts\reset-config.cmd [-DryRun] [-Backup] [-Force] [-IncludeSessions]
setlocal
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset-config.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset-config.ps1" %*
)
set "code=%errorlevel%"
if "%~1"=="" pause
exit /b %code%
