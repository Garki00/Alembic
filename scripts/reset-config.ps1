# scripts/reset-config.ps1
# 清除本机 Alembic（以及旧版 SmartAgent）的配置数据，方便重新查看初始化向导。
#
# 默认只删除配置目录（data/），保留会话历史（memory/）；
# 需要连会话一起清空时加 -IncludeSessions。
#
# 用法（仓库根目录）：
#   .\scripts\reset-config.ps1 -DryRun                 # 只列出将删除的目录
#   .\scripts\reset-config.ps1 -Backup                 # 删除前备份到 %TEMP%
#   .\scripts\reset-config.ps1 -Force                  # 不询问，直接删除
#   .\scripts\reset-config.ps1 -IncludeSessions -Force # 连会话历史一起清空
#
# 代码、.env、工作区里的文件都不会被改动。

param(
    [switch]$Force,
    [switch]$DryRun,
    [switch]$IncludeSessions,
    [switch]$KeepRepoSeed,
    [switch]$Backup
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Get-EnvValue([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return "" }
    return $value.Trim()
}

function Get-RoamingRoot {
    if (-not [string]::IsNullOrWhiteSpace($env:APPDATA)) { return $env:APPDATA }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        return Join-Path $env:USERPROFILE "AppData\Roaming"
    }
    return [IO.Path]::GetTempPath()
}

# 优先让应用自己的 settings 决定 app_home（会读取 .env 与 ALEMBIC_HOME）。
function Get-ConfiguredAlembicHome {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { return "" }
    Push-Location $repoRoot
    try {
        $lines = & $python -c "from config.settings import settings; print(settings.app_home)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $lines) {
            $value = ($lines | Select-Object -Last 1).ToString().Trim()
            if ($value) { return [IO.Path]::GetFullPath($value) }
        }
    } catch {
        # 读取失败时回落到环境变量 / APPDATA
    } finally {
        Pop-Location
    }
    return ""
}

function Get-AppHome([string]$EnvName, [string]$DefaultLeaf) {
    $custom = Get-EnvValue $EnvName
    if ($custom) {
        if (-not [IO.Path]::IsPathRooted($custom)) { $custom = Join-Path $repoRoot $custom }
        return [IO.Path]::GetFullPath($custom)
    }
    return [IO.Path]::GetFullPath((Join-Path (Get-RoamingRoot) $DefaultLeaf))
}

$roaming = Get-RoamingRoot
$defaultAlembic = [IO.Path]::GetFullPath((Join-Path $roaming "Alembic"))
$defaultSmart = [IO.Path]::GetFullPath((Join-Path $roaming "SmartAgent"))

$configuredAlembic = Get-ConfiguredAlembicHome
$alembicHome = if ($configuredAlembic) { $configuredAlembic } else { Get-AppHome "ALEMBIC_HOME" "Alembic" }
$smartHome = Get-AppHome "SMART_AGENT_HOME" "SmartAgent"

$script:targets = @()

function Add-Target([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $full = [IO.Path]::GetFullPath($Path)
    $leaf = Split-Path -Leaf $full
    if ($leaf -notin @("data", "memory", "logs")) { return }
    $parent = Split-Path -Parent $full
    if ([string]::IsNullOrWhiteSpace($parent)) { return }
    if ($parent -match '^[A-Za-z]:\\?$') { return }  # 拒绝 C:\data 这类危险目标
    if ($script:targets -notcontains $full) { $script:targets += $full }
}

# 当前生效目录 + 默认目录都清（ALEMBIC_HOME 自定义时两者可能不同）
foreach ($appHome in @($alembicHome, $defaultAlembic) | Select-Object -Unique) {
    Add-Target (Join-Path $appHome "data")
    if ($IncludeSessions) { Add-Target (Join-Path $appHome "memory") }
}
foreach ($appHome in @($smartHome, $defaultSmart) | Select-Object -Unique) {
    Add-Target (Join-Path $appHome "data")
    if ($IncludeSessions) { Add-Target (Join-Path $appHome "memory") }
}
if (-not $KeepRepoSeed) {
    Add-Target (Join-Path $repoRoot "data")
    if ($IncludeSessions) { Add-Target (Join-Path $repoRoot "memory") }
}

$existing = @($script:targets | Where-Object { Test-Path -LiteralPath $_ -PathType Container })

function Get-DirInfo([string]$Path) {
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    $size = 0
    foreach ($file in $files) { $size += $file.Length }
    return @{ Size = $size; Files = $files.Count }
}

function Format-Size([double]$Bytes) {
    if ($Bytes -ge 1GB) { return ("{0:N1} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N1} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N1} KB" -f ($Bytes / 1KB)) }
    return ("{0} B" -f [int]$Bytes)
}

Write-Host ""
Write-Host "ALEMBIC 配置重置" -ForegroundColor Cyan
Write-Host "将清除以下配置目录（代码、.env、工作区文件不受影响）："
if (-not $existing.Count) {
    Write-Host "  （没有找到需要清理的配置目录）" -ForegroundColor DarkGray
    exit 0
}
foreach ($target in $existing) {
    $info = Get-DirInfo $target
    Write-Host ("  - {0}  [{1}, {2} 个文件]" -f $target, (Format-Size $info.Size), $info.Files)
}
if (-not $IncludeSessions) {
    Write-Host "  会话历史（memory/）默认保留；要一起清空请加 -IncludeSessions。" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "提示：请先停止正在运行的后端/前端；运行中的进程可能把配置重新写回。" -ForegroundColor Yellow

$envFile = Join-Path $repoRoot ".env"
$hasKey = [bool]$env:DEEPSEEK_API_KEY
$hasModel = [bool]$env:MODEL_NAME
if (Test-Path -LiteralPath $envFile) {
    $envText = Get-Content -LiteralPath $envFile -Raw -ErrorAction SilentlyContinue
    if ($envText -match "(?m)^\s*DEEPSEEK_API_KEY\s*=\s*\S") { $hasKey = $true }
    if ($envText -match "(?m)^\s*MODEL_NAME\s*=\s*\S") { $hasModel = $true }
}
if ($hasKey -and $hasModel) {
    Write-Warning "检测到 DEEPSEEK_API_KEY 与 MODEL_NAME（来自 .env 或当前终端）；首次启动会自动生成供应商，不会进入初始化向导。需要看向导时请临时注释 MODEL_NAME。"
}

if ($DryRun) {
    Write-Host ""
    Write-Host "DryRun：未删除任何文件。" -ForegroundColor Cyan
    exit 0
}

if (-not $Force) {
    $answer = Read-Host "确认永久删除以上目录？输入 YES 继续"
    if ($answer -ne "YES") {
        Write-Host "已取消。"
        exit 0
    }
}

$backupPath = $null
if ($Backup) {
    $temp = $env:TEMP
    if ([string]::IsNullOrWhiteSpace($temp)) { $temp = [IO.Path]::GetTempPath() }
    $backupPath = Join-Path $temp ("alembic-config-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
    $index = 0
    foreach ($target in $existing) {
        $index += 1
        $parentLeaf = Split-Path -Leaf (Split-Path -Parent $target)
        $targetLeaf = Split-Path -Leaf $target
        $dest = Join-Path $backupPath ("{0:00}-{1}-{2}" -f $index, $parentLeaf, $targetLeaf)
        Copy-Item -LiteralPath $target -Destination $dest -Recurse -Force
    }
    Write-Host "已备份到：$backupPath" -ForegroundColor DarkYellow
    Write-Host "（备份包含 API Key，请妥善保管）" -ForegroundColor DarkYellow
}

$failed = 0
foreach ($target in $existing) {
    try {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        Write-Host ("  [OK] 已删除 {0}" -f $target) -ForegroundColor Green
    } catch {
        $failed += 1
        Write-Warning ("删除失败：{0} — {1}" -f $target, $_.Exception.Message)
    }
}

if ($failed) {
    Write-Warning ("有 {0} 个目录删除失败；请停止占用这些文件的进程后重试。" -f $failed)
    exit 1
}

Write-Host ""
Write-Host "完成。" -ForegroundColor Green
Write-Host "下次启动后端/前端时，如果 data 目录为空且 .env 没有同时配置 DEEPSEEK_API_KEY + MODEL_NAME，就会显示初始化向导。"
