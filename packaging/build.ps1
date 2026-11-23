# packaging/build.ps1
# 构建后端可执行文件：dist/alembic-backend/alembic-backend.exe
#
# 后端只提供 /api，不携带网页资源；前端由 frontend/ 构建，桌面外壳由
# desktop/ 打包（见 packaging/build-release.ps1）。本脚本只负责后端产物和
# 随包分发的可移植运行时。
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

& $python scripts/provision_runtimes.py
if ($LASTEXITCODE -ne 0) { throw "Portable runtime provisioning failed" }

$args = @("-m", "PyInstaller", "packaging\backend.spec", "--noconfirm")
if ($Clean) { $args += "--clean" }

Write-Host "开始构建后端…"
& $python @args
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller 构建失败" }

$exe = Join-Path $root "dist\alembic-backend\alembic-backend.exe"
if (-not (Test-Path $exe)) { Write-Error "构建产物不存在：$exe" }

$sizeMb = [math]::Round((Get-ChildItem (Split-Path $exe) -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "构建完成：$exe（$sizeMb MB）"
Write-Host "后端只提供 /api。前端请在 frontend 目录运行 npm run dev（或 npm run build）。"
Write-Host "完整安装包：powershell -ExecutionPolicy Bypass -File packaging\build-release.ps1"
