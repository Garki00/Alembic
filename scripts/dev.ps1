# scripts/dev.ps1
# 一键启动本地开发环境：FastAPI 后端（8000）+ Vite 前端（5173）。
#
# 用法（仓库根目录）：
#   .\scripts\dev.ps1
#   .\scripts\dev.ps1 -NoBrowser
#   .\scripts\dev.ps1 -BackendPort 8001 -FrontendPort 5174
#
# Ctrl+C 会同时结束后端与前端进程。

param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$NoBrowser,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python（先在仓库根目录运行 uv sync）"
}
if (-not (Test-Path (Join-Path $frontend "package.json"))) {
    throw "找不到前端目录：$frontend"
}

# 首次运行安装依赖。沙箱 / 受限终端里 npm 的 lifecycle 脚本可能被拒绝，
# 这里统一用 --ignore-scripts；Vue、Vite 都不需要 postinstall。
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    if ($SkipInstall) {
        throw "缺少 frontend\node_modules，先去 frontend 目录运行 npm install"
    }
    Write-Host "首次运行：安装前端依赖…"
    $env:npm_config_cache = Join-Path $root ".tmp\npm-cache"
    # 本机 npm registry 可能被配成二进制镜像（install 会 404），这里显式指定公开源；
    # 需要别的源时设置 ALEMBIC_NPM_REGISTRY。
    $registry = $env:ALEMBIC_NPM_REGISTRY
    if (-not $registry) {
        $registry = "https://registry.npmmirror.com"
    }
    Push-Location $frontend
    try {
        & npm install --ignore-scripts --no-audit --no-fund --registry=$registry
        if ($LASTEXITCODE -ne 0) { throw "npm install 失败（退出码 $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }
}

$logDir = Join-Path $root ".tmp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$backendOut = Join-Path $logDir "dev-backend.log"
$backendErr = Join-Path $logDir "dev-backend.err.log"
$frontendOut = Join-Path $logDir "dev-frontend.log"
$frontendErr = Join-Path $logDir "dev-frontend.err.log"

$env:WEB_PORT = "$BackendPort"
$env:ALEMBIC_PORT = "$BackendPort"
$env:FRONTEND_URL = "http://127.0.0.1:$FrontendPort"
$env:npm_config_cache = Join-Path $root ".tmp\npm-cache"
# 端口不是默认的 8000 时，让 Vite 的 /api 代理也跟着走
$env:VITE_BACKEND_URL = "http://127.0.0.1:$BackendPort"

Write-Host "启动后端：http://127.0.0.1:$BackendPort"
$backend = Start-Process -FilePath $python `
    -ArgumentList @("-m", "web") `
    -WorkingDirectory $root `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr `
    -PassThru -WindowStyle Hidden

$frontendProc = $null
try {
    $deadline = (Get-Date).AddSeconds(60)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 2 | Out-Null
            $healthy = $true
            break
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    if (-not $healthy) {
        Write-Host "后端启动失败，日志：$backendErr" -ForegroundColor Red
        Get-Content $backendErr -Tail 40 -ErrorAction SilentlyContinue
        throw "后端没有在 60 秒内就绪"
    }

    Write-Host "启动前端：http://127.0.0.1:$FrontendPort"
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npmCommand) {
        throw "找不到 npm，请先安装 Node.js 并确认它在 PATH 里"
    }
    $frontendProc = Start-Process -FilePath $npmCommand.Source `
        -ArgumentList @("run", "dev", "--", "--port", "$FrontendPort") `
        -WorkingDirectory $frontend `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -PassThru -WindowStyle Hidden

    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($frontendProc.HasExited) { break }
        try {
            Invoke-WebRequest "http://127.0.0.1:$FrontendPort" -TimeoutSec 2 -UseBasicParsing | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    if (-not $ready) {
        Write-Host "前端启动失败，日志：$frontendErr" -ForegroundColor Red
        Get-Content $frontendErr -Tail 40 -ErrorAction SilentlyContinue
        throw "前端没有在 60 秒内就绪"
    }

    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:$FrontendPort"
    }

    Write-Host ""
    Write-Host "ALEMBIC 已启动" -ForegroundColor Green
    Write-Host "  前端：http://127.0.0.1:$FrontendPort"
    Write-Host "  后端：http://127.0.0.1:$BackendPort/api"
    Write-Host "  日志：$logDir\dev-*.log"
    Write-Host "按 Ctrl+C 停止。"
    Write-Host ""

    Wait-Process -Id $frontendProc.Id
    if ($frontendProc.HasExited -and $frontendProc.ExitCode -ne 0) {
        Write-Host "前端进程退出（$($frontendProc.ExitCode)），日志：$frontendErr" -ForegroundColor Red
        Get-Content $frontendErr -Tail 40 -ErrorAction SilentlyContinue
    }
} finally {
    foreach ($process in @($frontendProc, $backend)) {
        if ($process -and -not $process.HasExited) {
            & taskkill /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
}
