# scripts/setup-sandbox.ps1
# 一次性管理员初始化：创建两个专用低权限用户、防火墙规则和 App 可读的凭据文件。
# 日常运行不需要管理员；应用本身不会以管理员身份运行。
param(
    [switch]$Verify,
    [switch]$Uninstall,
    [switch]$Visible,
    [int]$ProxyPort = 47831,
    # Windows 本地账户名上限 20 个字符，名字过长 New-LocalUser 会直接拒绝。
    [string]$OfflineUser = "AlembicSbxOffline",
    [string]$OnlineUser = "AlembicSbxOnline"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
$offlineUser = $OfflineUser
$onlineUser = $OnlineUser
$rulePrefix = "AlembicSandbox"
# Welcome 界面隐藏沙盒账户：值为 0 的账户不出现在登录/切换用户列表，
# 但 runas / LogonUser / CreateProcessWithLogonW 仍然正常。
$userListKey = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Set-SandboxUserHidden([string]$name, [bool]$hidden) {
    # 只处理本地账户；隐藏失败不应让整次初始化失败，沙盒本身照常可用。
    if ($null -eq (Get-LocalUser -Name $name -ErrorAction SilentlyContinue)) { return }
    try {
        if ($hidden) {
            if (-not (Test-Path -LiteralPath $userListKey)) {
                New-Item -Path $userListKey -Force | Out-Null
            }
            New-ItemProperty -Path $userListKey -Name $name -PropertyType DWord -Value 0 -Force | Out-Null
        } elseif (Test-Path -LiteralPath $userListKey) {
            Remove-ItemProperty -Path $userListKey -Name $name -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warning "无法隐藏账户 $name：$($_.Exception.Message)"
    }
}

if (-not (Test-Admin)) {
    throw "请以管理员身份运行：右键 PowerShell -> 以管理员身份运行，然后执行 scripts\setup-sandbox.ps1"
}

foreach ($name in @($offlineUser, $onlineUser)) {
    if ($name.Length -gt 20) {
        throw "沙盒账户名不能超过 20 个字符（Windows 本地账户限制）：$name 有 $($name.Length) 个字符，请用 -OfflineUser / -OnlineUser 改短。"
    }
}

$appHome = if ($env:ALEMBIC_HOME) { $env:ALEMBIC_HOME } else { Join-Path $env:APPDATA "Alembic" }
$dataDir = Join-Path $appHome "data"
$usersFile = Join-Path $dataDir "sandbox-users.json"
$setupFile = Join-Path $dataDir "sandbox-setup.json"
$sandboxFile = Join-Path $dataDir "sandbox.json"
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-ExpectedRules([string]$user, [bool]$allowProxy) {
    $names = @(
        "$rulePrefix-$user-BlockOut",
        "$rulePrefix-$user-BlockIn"
    )
    if ($allowProxy) {
        # 放行本机代理的做法不是“允许 + 覆盖阻止”（那要求 IPsec 认证/加密，
        # New-NetFirewallRule 会拒绝），而是把 127.0.0.1 上的 47831 从阻止范围里排除。
        $names += @(
            "$rulePrefix-$user-BlockOutV6",
            "$rulePrefix-$user-BlockOutLbTcp",
            "$rulePrefix-$user-BlockOutLbUdp"
        )
    }
    return $names
}

function Get-AllRuleNames([string]$user) {
    # 含旧版本留下的 -Proxy 规则名，初始化时顺手清掉。
    return @(Get-ExpectedRules $user $true) + @("$rulePrefix-$user-Proxy")
}

function Remove-Rules([string]$user) {
    foreach ($name in (Get-AllRuleNames $user)) {
        Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    }
}

function New-FirewallRule([hashtable]$Spec) {
    # New-NetFirewallRule 的错误是非终止错误，EAP=Stop 也不一定拦住；
    # 显式捕获并抛出，避免出现“规则没建好却仍标记初始化完成”。
    try {
        New-NetFirewallRule @Spec -ErrorAction Stop | Out-Null
    } catch {
        throw "创建防火墙规则「$($Spec.DisplayName)」失败：$($_.Exception.Message)"
    }
}

function Test-IPv6Enabled {
    # 只有拿到链路本地以外的 IPv6 地址，才算真的能走 IPv6 出网；
    # 查不到就返回 $null（只警告，不阻断初始化）。
    try {
        $global = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notmatch '^(fe80|::1)' })
        return $global.Count -gt 0
    } catch {
        return $null
    }
}

function Add-IPv6BlockRule([string]$name, [string]$sddl) {
    # 同一台机器上不同 Windows 版本对 IPv6 前缀的校验不一致（::/0 会被判成
    # “地址前缀无效”），所以按从宽到窄依次尝试；全失败再决定是否致命。
    $candidates = @(
        @("::/0"),
        @("::0/0"),
        @("2000::/3", "fc00::/7", "fe80::/10")
    )
    $last = ""
    foreach ($prefixes in $candidates) {
        try {
            New-FirewallRule @{
                DisplayName = $name
                Direction = "Outbound"; Action = "Block"; LocalUser = $sddl
                Profile = "Any"; Enabled = "True"; RemoteAddress = $prefixes
            }
            return $true
        } catch {
            $last = $_.Exception.Message
        }
    }
    $enabled = Test-IPv6Enabled
    if ($enabled -eq $true) {
        throw "无法创建 IPv6 阻止规则（$last）。系统启用了 IPv6，缺这条规则时沙盒可以绕过代理直接出网。"
    }
    Write-Warning "IPv6 阻止规则创建失败（$last）；系统未检测到启用的 IPv6 网卡，按可跳过处理。"
    return $false
}

function New-BlockRules([string]$user, [bool]$allowProxy) {
    $sid = (Get-LocalUser -Name $user).SID.Value
    $sddl = "D:(A;;CC;;;{0})" -f $sid
    Remove-Rules $user
    $created = @()

    if (-not $allowProxy) {
        # offline：完全断网（不限定地址 = IPv4 + IPv6 一起挡）
        New-FirewallRule @{
            DisplayName = "$rulePrefix-$user-BlockOut"
            Direction = "Outbound"; Action = "Block"; LocalUser = $sddl
            Profile = "Any"; Enabled = "True"
        }
        $created += "$rulePrefix-$user-BlockOut"
        New-FirewallRule @{
            DisplayName = "$rulePrefix-$user-BlockIn"
            Direction = "Inbound"; Action = "Block"; LocalUser = $sddl
            Profile = "Any"; Enabled = "True"
        }
        $created += "$rulePrefix-$user-BlockIn"
        return $created
    }

    # online：只允许 127.0.0.1:ProxyPort，其余 IPv4 外网、IPv6、本机其它端口全部阻止。
    New-FirewallRule @{
        DisplayName = "$rulePrefix-$user-BlockOut"
        Direction = "Outbound"; Action = "Block"; LocalUser = $sddl
        Profile = "Any"; Enabled = "True"
        RemoteAddress = @("0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255")
    }
    $created += "$rulePrefix-$user-BlockOut"
    if (Add-IPv6BlockRule "$rulePrefix-$user-BlockOutV6" $sddl) {
        $created += "$rulePrefix-$user-BlockOutV6"
    }
    $portRanges = @()
    if ($ProxyPort -gt 1) { $portRanges += "1-$($ProxyPort - 1)" }
    if ($ProxyPort -lt 65535) { $portRanges += "$($ProxyPort + 1)-65535" }
    if ($portRanges.Count -gt 0) {
        New-FirewallRule @{
            DisplayName = "$rulePrefix-$user-BlockOutLbTcp"
            Direction = "Outbound"; Action = "Block"; LocalUser = $sddl
            Profile = "Any"; Enabled = "True"; Protocol = "TCP"
            RemoteAddress = "127.0.0.0/8"; RemotePort = $portRanges
        }
        $created += "$rulePrefix-$user-BlockOutLbTcp"
    }
    New-FirewallRule @{
        DisplayName = "$rulePrefix-$user-BlockOutLbUdp"
        Direction = "Outbound"; Action = "Block"; LocalUser = $sddl
        Profile = "Any"; Enabled = "True"; Protocol = "UDP"
        RemoteAddress = "127.0.0.0/8"
    }
    $created += "$rulePrefix-$user-BlockOutLbUdp"
    New-FirewallRule @{
        DisplayName = "$rulePrefix-$user-BlockIn"
        Direction = "Inbound"; Action = "Block"; LocalUser = $sddl
        Profile = "Any"; Enabled = "True"
    }
    $created += "$rulePrefix-$user-BlockIn"
    return $created
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

function Write-JsonFile([string]$path, $value) {
    # Windows PowerShell 5.1 的 Set-Content -Encoding UTF8 会写 BOM，
    # Python json 读取时会失败；这里明确写无 BOM 的 UTF-8。
    $json = $value | ConvertTo-Json -Depth 6
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($path, $json, $utf8)
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
            -ArgumentList "/c exit" -Credential $credential -LoadUserProfile `
            -Wait -WindowStyle Hidden | Out-Null
    } catch {
        Write-Warning "无法预创建 $name 的用户配置文件：$($_.Exception.Message)"
    }
}

if ($Uninstall) {
    foreach ($name in @($offlineUser, $onlineUser)) {
        Remove-Rules $name
        Set-SandboxUserHidden $name $false
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
        $required = @("$rulePrefix-$name-BlockOut", "$rulePrefix-$name-BlockIn")
        if ($name -eq $onlineUser) {
            $required += @("$rulePrefix-$name-BlockOutLbTcp", "$rulePrefix-$name-BlockOutLbUdp")
        }
        foreach ($rule in $required) {
            if ($null -eq (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
                throw "缺少防火墙规则：$rule"
            }
        }
        if ($name -eq $onlineUser -and
            $null -eq (Get-NetFirewallRule -DisplayName "$rulePrefix-$name-BlockOutV6" -ErrorAction SilentlyContinue)) {
            Write-Host "提示：缺少 IPv6 阻止规则；如果本机启用了 IPv6，沙盒可以绕过代理出网。" -ForegroundColor Yellow
        }
        $hidden = $null
        if (Test-Path -LiteralPath $userListKey) {
            $item = Get-ItemProperty -Path $userListKey -Name $name -ErrorAction SilentlyContinue
            if ($null -ne $item) { $hidden = [int]$item.$name }
        }
        if ($hidden -ne 0) {
            Write-Host "提示：$name 仍会出现在登录界面（可用 scripts\hide-sandbox-users.ps1 隐藏）。" -ForegroundColor Yellow
        }
    }
    if (-not (Test-Path -LiteralPath $usersFile)) {
        throw "缺少 $usersFile"
    }
    Write-Host "沙盒初始化验证通过。" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
# 旧版本脚本用的名字（AlembicSandboxOffline 有 21 个字符，Windows 根本建不出来）。
$legacy = @(Get-LocalUser -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "Alembic*" -and $_.Name -ne $offlineUser -and $_.Name -ne $onlineUser } |
    ForEach-Object { $_.Name })
if ($legacy.Count -gt 0) {
    Write-Warning ("检测到旧版/手工创建的沙盒账户：{0}。本次不会改动它们，但它们仍会出现在登录界面；不再需要时可以 Remove-LocalUser -Name <名字>。" -f ($legacy -join ", "))
}
$offlinePassword = New-RandomPassword
$onlinePassword = New-RandomPassword
New-SandboxUser $offlineUser $offlinePassword
New-SandboxUser $onlineUser $onlinePassword
# 默认把两个账户从登录/切换用户界面隐藏，降低误登录和暴露面；
# 需要重新显示时运行 scripts\hide-sandbox-users.ps1 -Show，或用 -Visible 初始化。
if (-not $Visible) {
    Set-SandboxUserHidden $offlineUser $true
    Set-SandboxUserHidden $onlineUser $true
} else {
    Set-SandboxUserHidden $offlineUser $false
    Set-SandboxUserHidden $onlineUser $false
}

$createdRules = @()
$createdRules += New-BlockRules $offlineUser $false
$createdRules += New-BlockRules $onlineUser $true
# 规则没建好就不要写 ready：否则应用会以为沙盒可用，实际网络策略是缺的。
foreach ($rule in $createdRules) {
    if ($null -eq (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        throw "防火墙规则未创建成功：$rule（可先运行 setup-sandbox.ps1 -Uninstall 清理后重试）"
    }
}

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
Write-JsonFile $usersFile $payload
$setup = @{
    ready = $true
    version = 1
    proxy_port = $ProxyPort
    updated_at = (Get-Date).ToString("o")
}
Write-JsonFile $setupFile $setup

if (-not (Test-Path -LiteralPath $sandboxFile)) {
    Write-JsonFile $sandboxFile @{
        version = 1
        network = "offline"
        allowed_domains = @(
            "registry.npmjs.org", "registry.npmmirror.com", "pypi.org",
            "files.pythonhosted.org", "github.com", "codeload.github.com",
            "raw.githubusercontent.com"
        )
        allowed_ports = @(443, 80)
        proxy_port = $ProxyPort
    }
}

# 凭据文件只允许当前用户和 SYSTEM 读取。
# 用 .NET ACL API 而不是 icacls：icacls 在部分环境（含中文账户名/特殊路径）会
# 报“无效参数 /grant:r”，而这里只需要设置固定的两条规则。
function Protect-SandboxFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) {
            [void]$acl.RemoveAccessRule($rule)
        }
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $currentUser, "FullControl", "Allow")))
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            "SYSTEM", "FullControl", "Allow")))
        Set-Acl -LiteralPath $Path -AclObject $acl
    } catch {
        Write-Warning "无法为 $Path 设置权限：$($_.Exception.Message)"
    }
}

Protect-SandboxFile $usersFile
Protect-SandboxFile $setupFile

Write-Host ""
Write-Host "沙盒初始化完成。" -ForegroundColor Green
Write-Host "  用户：$offlineUser / $onlineUser"
Write-Host "  网络：offline 用户完全断网；allowlist 用户只能访问本机代理 $ProxyPort"
if ($Visible) {
    Write-Host "  登录界面：显示这两个账户（-Visible）"
} else {
    Write-Host "  登录界面：已隐藏这两个账户（恢复：scripts\hide-sandbox-users.ps1 -Show）"
}
Write-Host "  配置：$usersFile"
Write-Host "  验证：scripts\setup-sandbox.ps1 -Verify"
