# scripts/hide-sandbox-users.ps1
# 隐藏 / 恢复沙盒账户在登录界面（锁屏、切换用户）里的显示。
#
# Windows 没有“隐藏账户”的正式开关，这里用的是 Welcome 界面沿用了二十年的
# SpecialAccounts\UserList 机制：值为 0 的账户不出现在登录列表里，但仍然可以
# 用 runas、LogonUser、CreateProcessWithLogonW 正常登录——沙盒命令照常工作。
#
# 用法（管理员 PowerShell）：
#   .\scripts\hide-sandbox-users.ps1              # 隐藏脚本创建的沙盒账户
#   .\scripts\hide-sandbox-users.ps1 -Show        # 恢复显示
#   .\scripts\hide-sandbox-users.ps1 -Verify      # 只看当前状态
#   .\scripts\hide-sandbox-users.ps1 -User foo -DryRun
#
# 注意：隐藏只影响界面。账户在 `net user`、`lusrmgr.msc`、事件日志里仍然可见。

param(
    [string[]]$User = @(),
    [switch]$Show,
    [switch]$Verify,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$userListKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList"
$appHome = if ($env:ALEMBIC_HOME) { $env:ALEMBIC_HOME } else { Join-Path $env:APPDATA "Alembic" }
$usersFile = Join-Path (Join-Path $appHome "data") "sandbox-users.json"
# setup-sandbox.ps1 使用的账户名，作为读不到配置文件时的兜底
$defaultUsers = @("AlembicSbxOffline", "AlembicSbxOnline")

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-TargetUsers {
    if ($User.Count -gt 0) {
        return @($User | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
    }
    # 1) 沙盒凭据文件里记录的用户名最准确
    if (Test-Path -LiteralPath $usersFile) {
        try {
            $payload = Get-Content -LiteralPath $usersFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $names = @()
            foreach ($mode in @("offline", "online")) {
                $entry = $payload.$mode
                if ($entry -and $entry.username) { $names += [string]$entry.username }
            }
            if ($names.Count -gt 0) { return @($names | Select-Object -Unique) }
        } catch {
            Write-Warning "读取 $usersFile 失败，改用默认账户名：$($_.Exception.Message)"
        }
    }
    # 2) 默认名存在就用默认名
    $existing = @($defaultUsers | Where-Object { Get-LocalUser -Name $_ -ErrorAction SilentlyContinue })
    if ($existing.Count -gt 0) { return $existing }
    # 3) 兜底：本地账户里以 Alembic 开头的（手工创建的 AlembicOnline 也覆盖到）
    $found = @(Get-LocalUser -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "Alembic*" } |
        ForEach-Object { $_.Name })
    return $found
}

function Get-HiddenValue([string]$name) {
    if (-not (Test-Path -LiteralPath $userListKey)) { return $null }
    $item = Get-ItemProperty -Path $userListKey -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    return [int]$item.$name
}

function Set-Hidden([string]$name) {
    if (-not (Test-Path -LiteralPath $userListKey)) {
        New-Item -Path $userListKey -Force | Out-Null
    }
    New-ItemProperty -Path $userListKey -Name $name -PropertyType DWord -Value 0 -Force | Out-Null
}

function Set-Visible([string]$name) {
    if (-not (Test-Path -LiteralPath $userListKey)) { return }
    Remove-ItemProperty -Path $userListKey -Name $name -ErrorAction SilentlyContinue
}

$targets = @(Get-TargetUsers)
if ($targets.Count -eq 0) {
    Write-Host "没有找到沙盒账户。用 -User <用户名> 指定，或先运行 scripts\setup-sandbox.ps1。" -ForegroundColor Yellow
    exit 1
}

$missing = @($targets | Where-Object { -not (Get-LocalUser -Name $_ -ErrorAction SilentlyContinue) })
$targets = @($targets | Where-Object { $missing -notcontains $_ })
foreach ($name in $missing) {
    Write-Warning "本地账户不存在，跳过：$name"
}
if ($targets.Count -eq 0) {
    Write-Host "没有可处理的账户。" -ForegroundColor Yellow
    exit 1
}

if ($Verify) {
    foreach ($name in $targets) {
        $value = Get-HiddenValue $name
        $state = if ($null -eq $value) { "显示（未设置）" } elseif ($value -eq 0) { "已隐藏" } else { "显示（= $value）" }
        Write-Host ("{0}: {1}" -f $name, $state)
    }
    exit 0
}

if (-not $DryRun -and -not (Test-Admin)) {
    throw "请以管理员身份运行：右键 PowerShell -> 以管理员身份运行，然后执行 scripts\hide-sandbox-users.ps1"
}

$action = if ($Show) { "显示" } else { "隐藏" }
foreach ($name in $targets) {
    if ($DryRun) {
        Write-Host ("[DryRun] 将{0}：{1}" -f $action, $name)
        continue
    }
    if ($Show) {
        Set-Visible $name
    } else {
        Set-Hidden $name
    }
    Write-Host ("已{0}：{1}" -f $action, $name) -ForegroundColor Green
}

if (-not $Show -and -not $DryRun) {
    Write-Host ""
    Write-Host "隐藏只影响登录/切换用户界面：锁屏或注销后生效。" -ForegroundColor DarkGray
    Write-Host "runas、LogonUser、CreateProcessWithLogonW 仍然可以正常登录这些账户。" -ForegroundColor DarkGray
    Write-Host "恢复显示：.\scripts\hide-sandbox-users.ps1 -Show" -ForegroundColor DarkGray
}
