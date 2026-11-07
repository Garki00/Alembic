# packaging/build.ps1
# 构建后端可执行文件：dist/alembic-backend/alembic-backend.exe
#
# 注意：前后端分离后，这个后端只提供 /api，不再携带网页资源。
# 桌面外壳（desktop/）尚未适配 Vue 前端，暂不在本脚本范围内。
#
# 用法（仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Clean

param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "找不到虚拟环境：$python（先运行 uv sync）"
}

$hasPyInstaller = & $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "未安装 PyInstaller，正在安装…"
    $env:UV_CACHE_DIR = Join-Path $root ".tmp\uv-cache"
    uv pip install --python $python pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Error "安装 PyInstaller 失败" }
}

$args = @("-m", "PyInstaller", "packaging\backend.spec", "--noconfirm")
if ($Clean) { $args += "--clean" }

Write-Host "开始构建后端…"
& $python @args
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller 构建失败" }

$exe = Join-Path $root "dist\alembic-backend\alembic-backend.exe"
if (-not (Test-Path $exe)) { Write-Error "构建产物不存在：$exe" }

# 浏览器模式启动器暂不适配前后端分离：后端只提供 /api，不再返回页面。
# 需要网页界面时请用 frontend/ 的 Vite 应用（见 README「网页对话工作区」）。
$launcher = Join-Path $root "packaging\browser-launcher.cmd"
if (Test-Path $launcher) {
    Copy-Item $launcher (Join-Path $root "dist\alembic-backend\说明（浏览器模式）.cmd") -Force
}

$sizeMb = [math]::Round((Get-ChildItem (Split-Path $exe) -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "构建完成：$exe（$sizeMb MB）"
Write-Host "后端只提供 /api。前端请在 frontend 目录运行 npm run dev（或 npm run build）。"
