# scripts/setup-sandbox.ps1
# 一次性管理员初始化：创建两个专用低权限用户、防火墙规则和 App 可读的凭据文件。
# 日常运行不需要管理员；应用本身不会以管理员身份运行。
param(
    [switch]$Verify,
    [switch]$Uninstall,
    [int]$ProxyPort = 47831
)

$ErrorActionPreference = "Stop"
$offlineUser = "AlembicOffline"
$onlineUser = "AlembicOnline"
$rulePrefix = "AlembicSandbox"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    throw "请以管理员身份运行：右键 PowerShell -> 以管理员身份运行，然后执行 scripts\setup-sandbox.ps1"
}

$appHome = if ($env:ALEMBIC_HOME) { $env:ALEMBIC_HOME } else { Join-Path $env:APPDATA "Alembic" }
$dataDir = Join-Path $appHome "data"
$usersFile = Join-Path $dataDir "sandbox-users.json"
$setupFile = Join-Path $dataDir "sandbox-setup.json"
$sandboxFile = Join-Path $dataDir "sandbox.json"
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-RuleNames([string]$user) {
    return @(
        "$rulePrefix-$user-BlockOut",
        "$rulePrefix-$user-BlockIn",
        "$rulePrefix-$user-Proxy"
    )
}

function Remove-Rules([string]$user) {
    foreach ($name in (Get-RuleNames $user)) {
        Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    }
}

function New-BlockRules([string]$user) {
    $sid = (Get-LocalUser -Name $user).SID.Value
    $sddl = "D:(A;;CC;;;{0})" -f $sid
    Remove-Rules $user
    New-NetFirewallRule -DisplayName "$rulePrefix-$user-BlockOut" `
        -Direction Outbound -Action Block -LocalUser $sddl -Profile Any -Enabled True | Out-Null
    New-NetFirewallRule -DisplayName "$rulePrefix-$user-BlockIn" `
        -Direction Inbound -Action Block -LocalUser $sddl -Profile Any -Enabled True | Out-Null
}

function New-ProxyRule([string]$user) {
    $sid = (Get-LocalUser -Name $user).SID.Value
    $sddl = "D:(A;;CC;;;{0})" -f $sid
    New-NetFirewallRule -DisplayName "$rulePrefix-$user-Proxy" `
        -Direction Outbound -Action Allow -Protocol TCP `
        -RemoteAddress 127.0.0.1 -RemotePort $ProxyPort `
        -LocalUser $sddl -OverrideBlockRules $True -Profile Any -Enabled True | Out-Null
}

function Protect-Text([string]$text) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
    $protected = [Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [Convert]::ToBase64String($protected)
}

function New-RandomPassword {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes) + "aA1!"
}

function New-SandboxUser([string]$name, [string]$password) {
    $existing = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        $secure = ConvertTo-SecureString $password -AsPlainText -Force
        New-LocalUser -Name $name -Password $secure -PasswordNeverExpires `
            -AccountNeverExpires -UserMayNotChangePassword `
            -Description "ALEMBIC command sandbox" | Out-Null
    } else {
        $secure = ConvertTo-SecureString $password -AsPlainText -Force
        Set-LocalUser -Name $name -Password $secure -PasswordNeverExpires $true `
            -UserMayChangePassword $false
    }
    Add-LocalGroupMember -Group "Users" -Member $name -ErrorAction SilentlyContinue
    # Trigger Windows profile creation so LOGON_WITH_PROFILE does not fail or
    # create a suffixed profile on the first command.
    try {
        $secure = ConvertTo-SecureString $password -AsPlainText -Force
        $credential = New-Object System.Management.Automation.PSCredential($name, $secure)
        Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" `
            -ArgumentList "/c exit" -Credential $credential -Wait -WindowStyle Hidden | Out-Null
    } catch {
        Write-Warning "无法预创建 $name 的用户配置文件：$($_.Exception.Message)"
    }
}

if ($Uninstall) {
    foreach ($name in @($offlineUser, $onlineUser)) {
        Remove-Rules $name
        $existing = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            Remove-LocalUser -Name $name
        }
    }
    Remove-Item -LiteralPath $usersFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $setupFile -Force -ErrorAction SilentlyContinue
    Write-Host "已移除沙盒用户、防火墙规则和配置。" -ForegroundColor Green
    exit 0
}

if ($Verify) {
    foreach ($name in @($offlineUser, $onlineUser)) {
        if ($null -eq (Get-LocalUser -Name $name -ErrorAction SilentlyContinue)) {
            throw "缺少沙盒用户：$name"
        }
        foreach ($rule in (Get-RuleNames $name)) {
            if ($null -eq (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
                throw "缺少防火墙规则：$rule"
            }
        }
    }
    if (-not (Test-Path -LiteralPath $usersFile)) {
        throw "缺少 $usersFile"
    }
    Write-Host "沙盒初始化验证通过。" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$offlinePassword = New-RandomPassword
$onlinePassword = New-RandomPassword
New-SandboxUser $offlineUser $offlinePassword
New-SandboxUser $onlineUser $onlinePassword

New-BlockRules $offlineUser
New-BlockRules $onlineUser
New-ProxyRule $onlineUser

$offlineProfile = Join-Path $env:SystemDrive "Users\$offlineUser"
$onlineProfile = Join-Path $env:SystemDrive "Users\$onlineUser"
$payload = @{
    offline = @{
        username = $offlineUser
        password = Protect-Text $offlinePassword
        profile = $offlineProfile
    }
    online = @{
        username = $onlineUser
        password = Protect-Text $onlinePassword
        profile = $onlineProfile
    }
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $usersFile -Encoding UTF8
$setup = @{
    ready = $true
    version = 1
    proxy_port = $ProxyPort
    updated_at = (Get-Date).ToString("o")
}
$setup | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $setupFile -Encoding UTF8

if (-not (Test-Path -LiteralPath $sandboxFile)) {
    @{
        version = 1
        network = "offline"
        allowed_domains = @(
            "registry.npmjs.org", "registry.npmmirror.com", "pypi.org",
            "files.pythonhosted.org", "github.com", "codeload.github.com",
            "raw.githubusercontent.com"
        )
        allowed_ports = @(443, 80)
        proxy_port = $ProxyPort
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $sandboxFile -Encoding UTF8
}

# 凭据文件只允许当前用户和 SYSTEM 读取。
& icacls $usersFile /inheritance:r /grant:r "$currentUser:F" /grant:r "SYSTEM:F" | Out-Null
& icacls $setupFile /inheritance:r /grant:r "$currentUser:F" /grant:r "SYSTEM:F" | Out-Null

Write-Host ""
Write-Host "沙盒初始化完成。" -ForegroundColor Green
Write-Host "  用户：$offlineUser / $onlineUser"
Write-Host "  网络：offline 用户完全断网；allowlist 用户只能访问本机代理 $ProxyPort"
Write-Host "  配置：$usersFile"
Write-Host "  验证：scripts\setup-sandbox.ps1 -Verify"
