#Requires -Version 5.1
<#
.SYNOPSIS
    ALEMBIC 提权卸载 worker。

.DESCRIPTION
    由 desktop/main.js 的卸载界面通过 UAC 启动。删除快捷方式、注册表项、
    沙盒账户和可选的用户数据，最后安排一个清理进程在卸载界面退出后删除
    安装目录（Windows 不允许删除正在运行的 exe）。

    进度以 JSONL 追加写入 -ProgressFile。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$ProgressFile,
    [string]$LogFile = "",
    [string]$AppHome = "",
    [string]$LocalAppData = "",
    [string]$OwnerName = "",
    [int]$UninstallerPid = 0,
    [switch]$RemoveSandbox,
    [switch]$RemoveUserData
)

$ErrorActionPreference = "Stop"
# 卸载 worker 的当前目录可能正好是安装目录（快捷方式设置的工作目录）。
# 清理进程不能以安装目录为 CWD，否则 Windows 不允许删除它。
try { Set-Location -LiteralPath ([IO.Path]::GetTempPath()) } catch { }
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
$script:ProgressPath = [IO.Path]::GetFullPath($ProgressFile)
$script:LogPath = if ($LogFile) { [IO.Path]::GetFullPath($LogFile) } else { "" }
$script:CurrentStep = ""
$script:Warnings = New-Object System.Collections.ArrayList
$script:FullInstallDir = ""
$script:Receipt = $null

$progressDir = Split-Path -Parent $script:ProgressPath
if ($progressDir -and -not (Test-Path -LiteralPath $progressDir)) {
    New-Item -ItemType Directory -Path $progressDir -Force | Out-Null
}
if ($script:LogPath) {
    $logDir = Split-Path -Parent $script:LogPath
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
}

function Write-Event {
    param(
        [string]$Event,
        [string]$Id = "",
        [string]$Status = "",
        [string]$Message = "",
        [int]$Percent = -1,
        [hashtable]$Detail = @{}
    )
    $payload = [ordered]@{
        event = $Event
        time  = (Get-Date).ToString("o")
    }
    if ($Id) { $payload["id"] = $Id }
    if ($Status) { $payload["status"] = $Status }
    if ($Message) { $payload["message"] = $Message }
    if ($Percent -ge 0) { $payload["percent"] = $Percent }
    if ($Detail -and $Detail.Count -gt 0) { $payload["detail"] = $Detail }
    try {
        [IO.File]::AppendAllText(
            $script:ProgressPath,
            (($payload | ConvertTo-Json -Compress -Depth 6) + "`n"),
            $script:Utf8
        )
    } catch {
        if ($script:LogPath) {
            try { [IO.File]::AppendAllText($script:LogPath, "写进度失败：$($_.Exception.Message)`n", $script:Utf8) } catch { }
        }
    }
}

function Write-Log {
    param([string]$Message)
    if (-not $Message) { return }
    Write-Event -Event "log" -Message $Message
    if ($script:LogPath) {
        try {
            [IO.File]::AppendAllText(
                $script:LogPath,
                ("[" + (Get-Date).ToString("o") + "] " + $Message + "`n"),
                $script:Utf8
            )
        } catch { }
    }
}

function Add-Warning {
    param([string]$Message)
    [void]$script:Warnings.Add($Message)
    Write-Log "警告：$Message"
}

function Start-Step {
    param([string]$Id, [string]$Message, [int]$Percent)
    $script:CurrentStep = $Id
    Write-Event -Event "step" -Id $Id -Status "running" -Message $Message -Percent $Percent
    Write-Host $Message
}

function Complete-Step {
    param([string]$Id, [string]$Message, [int]$Percent)
    Write-Event -Event "step" -Id $Id -Status "done" -Message $Message -Percent $Percent
    Write-Host $Message
}

function Skip-Step {
    param([string]$Id, [string]$Message, [int]$Percent)
    Write-Event -Event "step" -Id $Id -Status "skipped" -Message $Message -Percent $Percent
    Write-Host $Message
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "卸载 ALEMBIC 需要管理员权限。"
    }
}

function Get-SafeDirectory {
    param([string]$Path)
    if (-not $Path) { return "" }
    try {
        $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    } catch {
        return ""
    }
    $root = [IO.Path]::GetPathRoot($full).TrimEnd('\')
    if ($full.Length -le 3 -or $full -ieq $root) { return "" }
    return $full
}

function Remove-PathWithRetry {
    param([string]$Path, [int]$Attempts = 5)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $true }
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            if ($attempt -eq $Attempts) {
                Add-Warning "无法删除 $Path：$($_.Exception.Message)"
                return $false
            }
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Stop-App {
    $names = @("ALEMBIC", "alembic-backend")
    foreach ($name in $names) {
        $processes = @(Get-Process -Name $name -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -ne $UninstallerPid })
        foreach ($process in $processes) {
            try { $process.CloseMainWindow() | Out-Null } catch { }
        }
    }
    Start-Sleep -Seconds 2
    foreach ($name in $names) {
        Get-Process -Name $name -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -ne $UninstallerPid } |
            ForEach-Object {
                if ($name -eq "alembic-backend") {
                    & taskkill /PID $_.Id /T /F 2>&1 | Out-Null
                } else {
                    try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { }
                }
            }
    }
    Start-Sleep -Milliseconds 500
}

function Remove-Shortcuts {
    $targets = New-Object System.Collections.ArrayList
    if ($script:Receipt -and $script:Receipt.shortcuts) {
        foreach ($item in @($script:Receipt.shortcuts)) {
            if ($item) { [void]$targets.Add([string]$item) }
        }
    }
    $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $programs = [Environment]::GetFolderPath("CommonPrograms")
    if ($desktop) { [void]$targets.Add((Join-Path $desktop "ALEMBIC.lnk")) }
    if ($programs) { [void]$targets.Add((Join-Path $programs "ALEMBIC")) }

    $seen = @{}
    foreach ($path in $targets) {
        if (-not $path) { continue }
        $key = $path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
            Write-Log "已删除快捷方式：$path"
        } catch {
            Add-Warning "删除快捷方式失败：$path（$($_.Exception.Message)）"
        }
    }
}

function Remove-Sandbox {
    $setup = Join-Path $script:FullInstallDir "resources\install\setup-sandbox.ps1"
    if (-not (Test-Path -LiteralPath $setup)) {
        Add-Warning "安装目录里缺少 setup-sandbox.ps1，跳过沙盒清理。"
        return
    }
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $previousHome = $env:ALEMBIC_HOME
    if ($AppHome) { $env:ALEMBIC_HOME = $AppHome }
    try {
        $arguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $setup, "-Uninstall"
        )
        & $powershell @arguments 2>&1 | ForEach-Object { Write-Log ([string]$_) }
        if ($LASTEXITCODE -ne 0) {
            Add-Warning "setup-sandbox.ps1 -Uninstall 退出码 $LASTEXITCODE。"
        } else {
            Write-Log "已移除沙盒账户与防火墙规则。"
        }
    } catch {
        Add-Warning "移除沙盒失败：$($_.Exception.Message)"
    } finally {
        $env:ALEMBIC_HOME = $previousHome
    }
}

function Remove-UserData {
    $candidates = New-Object System.Collections.ArrayList
    foreach ($path in @($AppHome, $LocalAppData)) {
        $safe = Get-SafeDirectory $path
        if ($safe) { [void]$candidates.Add($safe) }
    }
    $seen = @{}
    foreach ($path in $candidates) {
        $key = $path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (-not (Test-Path -LiteralPath $path)) { continue }
        if (Remove-PathWithRetry -Path $path) {
            Write-Log "已删除用户数据：$path"
        }
    }
}

function Remove-Registry {
    $uninstallKey = if ($script:Receipt -and $script:Receipt.registry -and $script:Receipt.registry.uninstall_key) {
        [string]$script:Receipt.registry.uninstall_key
    } else {
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ALEMBIC"
    }
    $appPathKey = if ($script:Receipt -and $script:Receipt.registry -and $script:Receipt.registry.app_path_key) {
        [string]$script:Receipt.registry.app_path_key
    } else {
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ALEMBIC.exe"
    }

    foreach ($key in @($uninstallKey, $appPathKey)) {
        if (-not $key) { continue }
        if (-not (Test-Path -LiteralPath $key)) { continue }
        try {
            Remove-Item -LiteralPath $key -Recurse -Force -ErrorAction Stop
            Write-Log "已删除注册表项：$key"
        } catch {
            Add-Warning "删除注册表项失败：$key（$($_.Exception.Message)）"
        }
    }
}

function Remove-InstallDir {
    param([string]$Directory)
    $full = Get-SafeDirectory $Directory
    if (-not $full) { throw "安装目录路径无效：$Directory" }
    # 卸载器从 %TEMP% 副本运行，安装目录里没有正在执行的文件，所以这里可以同步
    # 删除，页面不必等窗口关闭。杀软/索引器可能短暂占用，重试最多 60 秒。
    for ($attempt = 1; $attempt -le 120; $attempt++) {
        try {
            Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
            Write-Log "已删除安装目录：$full"
            return
        } catch {
            if ($attempt -eq 120) {
                throw "无法删除安装目录 $full：$($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

try {
    Assert-Admin
    $script:FullInstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $script:FullInstallDir "ALEMBIC.exe"))) {
        throw "安装目录里找不到 ALEMBIC.exe：$script:FullInstallDir"
    }
    $receiptFile = Join-Path $script:FullInstallDir "install.json"
    if (-not (Test-Path -LiteralPath $receiptFile)) {
        throw "安装目录里找不到 install.json，无法确认安装信息。"
    }

    Start-Step "prepare" "读取安装信息…" 3
    $script:Receipt = Get-Content -LiteralPath $receiptFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Complete-Step "prepare" ("ALEMBIC " + $script:Receipt.version + " 已找到。") 5

    Start-Step "stop" "停止正在运行的 ALEMBIC…" 8
    Stop-App
    Complete-Step "stop" "应用已停止。" 14

    Start-Step "shortcuts" "删除快捷方式…" 18
    Remove-Shortcuts
    Complete-Step "shortcuts" "快捷方式已处理。" 24

    if ($RemoveSandbox) {
        Start-Step "sandbox" "移除命令沙盒账户与防火墙规则…" 28
        Remove-Sandbox
        Complete-Step "sandbox" "命令沙盒已处理。" 42
    } else {
        Skip-Step "sandbox" "保留命令沙盒账户。" 42
    }

    if ($RemoveUserData) {
        Start-Step "userdata" "删除用户数据…" 46
        Remove-UserData
        Complete-Step "userdata" "用户数据已处理。" 62
    } else {
        Skip-Step "userdata" "保留用户数据。" 62
    }

    Start-Step "registry" "移除卸载注册信息…" 66
    Remove-Registry
    Complete-Step "registry" "注册信息已处理。" 76

    Start-Step "cleanup" "删除安装目录…" 82
    Remove-InstallDir $script:FullInstallDir
    Complete-Step "cleanup" "安装目录已删除。" 95

    $message = "ALEMBIC 已卸载。"
    if ($script:Warnings.Count -gt 0) {
        $message = "ALEMBIC 已卸载，但有 $($script:Warnings.Count) 项需要手动处理，详见日志。"
    }
    Write-Event -Event "result" -Status "ok" -Message $message -Percent 100 -Detail @{
        install_dir = $script:FullInstallDir
        warnings    = @($script:Warnings)
    }
    Write-Host $message
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Host "卸载失败：$message" -ForegroundColor Red
    if ($script:CurrentStep) {
        Write-Event -Event "step" -Id $script:CurrentStep -Status "error" -Message $message
    }
    Write-Event -Event "result" -Status "error" -Message $message -Detail @{
        warnings = @($script:Warnings)
    }
    exit 1
}
