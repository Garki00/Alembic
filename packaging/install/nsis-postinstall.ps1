#Requires -Version 5.1
<#
.SYNOPSIS
    Complete an ALEMBIC installation after the NSIS installer has extracted files.

.DESCRIPTION
    The NSIS installer owns the file payload and the native wizard. This script
    performs the environment-specific work that needs real Windows APIs:
    runtime verification, command-sandbox creation, shortcuts, uninstall
    registration, and the install receipt consumed by the Electron uninstaller.

    It is invoked by packaging/nsis/installer.nsh and is never called from the
    application at runtime.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$AppExe,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Publisher = "Garki",
    [int]$DesktopShortcut = 1,
    [int]$StartMenuShortcut = 1,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
$script:LogPath = if ($LogFile) { [IO.Path]::GetFullPath($LogFile) } else { "" }
$script:InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$script:BaseName = [IO.Path]::GetFileNameWithoutExtension($AppExe)
$script:UninstallerName = "$($script:BaseName)-Uninstall.exe"
$script:AppHome = if ($env:ALEMBIC_HOME) {
    [IO.Path]::GetFullPath($env:ALEMBIC_HOME)
} else {
    Join-Path $env:APPDATA "Alembic"
}
$script:LocalAppData = Join-Path $env:LOCALAPPDATA "Alembic"
$script:UninstallKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$($script:BaseName)"
$script:AppPathKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$AppExe"
$script:CreatedShortcuts = New-Object System.Collections.ArrayList
$script:RegistryTouched = $false
$script:AppPathTouched = $false
$script:SandboxCreated = $false
$script:SandboxPreexisting = $false
$script:UninstallerCreated = $false
$script:EstimatedSizeKb = 0

try { Set-Location -LiteralPath ([IO.Path]::GetTempPath()) } catch { }

if ($script:LogPath) {
    $logDir = Split-Path -Parent $script:LogPath
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
}

function Write-Log {
    param([string]$Message)
    if (-not $Message) { return }
    Write-Output $Message
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

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Installing ALEMBIC requires administrator privileges."
    }
}

function Get-DirectorySize {
    param([string]$Path)
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return [int64]0 }
    return [int64]$sum
}

function Assert-Runtime {
    $runtime = Join-Path $script:InstallDir "resources\backend\runtimes\win32-x64"
    $checks = @(
        @{ Name = "Node"; Exe = (Join-Path $runtime "node\node.exe"); Args = @("--version") },
        @{ Name = "Python"; Exe = (Join-Path $runtime "python\python.exe"); Args = @("--version") },
        @{ Name = "pnpm"; Exe = (Join-Path $runtime "bin\pnpm.cmd"); Args = @("--version") }
    )
    foreach ($check in $checks) {
        if (-not (Test-Path -LiteralPath $check.Exe)) {
            throw "Missing bundled runtime: $($check.Exe)"
        }
        $output = & $check.Exe @($check.Args) 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ("Runtime check failed: {0} (exit {1}) {2}" -f $check.Name, $LASTEXITCODE, ($output -join " "))
        }
        Write-Log ("[runtime] {0} {1}" -f $check.Name, ($output -join " "))
    }
}

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$Arguments = "",
        [string]$Description = "",
        [string]$Icon = "",
        [string]$WorkingDirectory = ""
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    if ($Arguments) { $link.Arguments = $Arguments }
    if ($Description) { $link.Description = $Description }
    if ($Icon) { $link.IconLocation = $Icon }
    if ($WorkingDirectory) { $link.WorkingDirectory = $WorkingDirectory }
    $link.Save()
    [void]$script:CreatedShortcuts.Add($Path)
    Write-Log "[shortcut] $Path"
}

function Install-Shortcuts {
    $exe = Join-Path $script:InstallDir $AppExe
    $uninstaller = Join-Path $script:InstallDir $script:UninstallerName
    $icon = "$exe,0"
    $description = $script:BaseName

    if ($DesktopShortcut -ne 0) {
        $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
        if (-not $desktop) { throw "Unable to locate the common desktop directory." }
        New-Shortcut -Path (Join-Path $desktop "$($script:BaseName).lnk") -Target $exe `
            -Description $description -Icon $icon -WorkingDirectory $script:InstallDir
    }

    if ($StartMenuShortcut -ne 0) {
        $programs = [Environment]::GetFolderPath("CommonPrograms")
        if (-not $programs) { throw "Unable to locate the common Start Menu directory." }
        $folder = Join-Path $programs $script:BaseName
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        [void]$script:CreatedShortcuts.Add($folder)
        New-Shortcut -Path (Join-Path $folder "$($script:BaseName).lnk") -Target $exe `
            -Description $description -Icon $icon -WorkingDirectory $script:InstallDir
        New-Shortcut -Path (Join-Path $folder "卸载 $($script:BaseName).lnk") -Target $uninstaller `
            -Arguments "--uninstall" -Description "卸载 $($script:BaseName)" -Icon $icon `
            -WorkingDirectory $script:InstallDir
    }
}

function Install-Sandbox {
    $setup = Join-Path $script:InstallDir "resources\install\setup-sandbox.ps1"
    if (-not (Test-Path -LiteralPath $setup)) {
        throw "Missing sandbox setup script: $setup"
    }

    $offline = $null -ne (Get-LocalUser -Name "AlembicSbxOffline" -ErrorAction SilentlyContinue)
    $online = $null -ne (Get-LocalUser -Name "AlembicSbxOnline" -ErrorAction SilentlyContinue)
    $script:SandboxPreexisting = ($offline -and $online)
    $script:SandboxCreated = -not $script:SandboxPreexisting

    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $previousHome = $env:ALEMBIC_HOME
    $env:ALEMBIC_HOME = $script:AppHome
    try {
        & $powershell -NoProfile -ExecutionPolicy Bypass -File $setup 2>&1 |
            ForEach-Object { Write-Log ("[sandbox] " + [string]$_) }
        if ($LASTEXITCODE -ne 0) {
            throw "Sandbox setup failed with exit code $LASTEXITCODE."
        }
    } finally {
        $env:ALEMBIC_HOME = $previousHome
    }

    foreach ($user in @("AlembicSbxOffline", "AlembicSbxOnline")) {
        if ($null -eq (Get-LocalUser -Name $user -ErrorAction SilentlyContinue)) {
            throw "Sandbox account was not created: $user"
        }
        if ($null -eq (Get-NetFirewallRule -DisplayName "AlembicSandbox-$user-BlockOut" -ErrorAction SilentlyContinue)) {
            throw "Sandbox firewall rule was not created for $user."
        }
        if ($null -eq (Get-NetFirewallRule -DisplayName "AlembicSandbox-$user-BlockIn" -ErrorAction SilentlyContinue)) {
            throw "Sandbox firewall rule was not created for $user."
        }
    }
}

function Write-UninstallRegistry {
    if (-not (Test-Path -LiteralPath $script:UninstallKey)) {
        New-Item -Path $script:UninstallKey -Force | Out-Null
    }
    $script:RegistryTouched = $true
    $exe = Join-Path $script:InstallDir $AppExe
    $uninstaller = Join-Path $script:InstallDir $script:UninstallerName
    if ($script:EstimatedSizeKb -le 0) {
        $script:EstimatedSizeKb = [int][Math]::Ceiling((Get-DirectorySize $script:InstallDir) / 1KB)
    }
    $sizeKb = $script:EstimatedSizeKb

    $values = [ordered]@{
        DisplayName = $script:BaseName
        DisplayVersion = $Version
        Publisher = $Publisher
        InstallLocation = $script:InstallDir
        DisplayIcon = "$exe,0"
        UninstallString = ('"{0}" --uninstall' -f $uninstaller)
        QuietUninstallString = ('"{0}" --uninstall' -f $uninstaller)
        NoModify = 1
        NoRepair = 1
        InstallDate = (Get-Date).ToString("yyyyMMdd")
        EstimatedSize = $sizeKb
    }
    foreach ($name in $values.Keys) {
        $type = "String"
        if ($values[$name] -is [int]) { $type = "DWord" }
        New-ItemProperty -Path $script:UninstallKey -Name $name -Value $values[$name] `
            -PropertyType $type -Force | Out-Null
    }

    if (-not (Test-Path -LiteralPath $script:AppPathKey)) {
        New-Item -Path $script:AppPathKey -Force | Out-Null
    }
    $script:AppPathTouched = $true
    Set-Item -Path $script:AppPathKey -Value $exe
    New-ItemProperty -Path $script:AppPathKey -Name "Path" -Value $script:InstallDir `
        -PropertyType String -Force | Out-Null
}

function Write-Receipt {
    $receipt = [ordered]@{
        schema = 1
        product = "ALEMBIC"
        version = $Version
        publisher = $Publisher
        install_dir = $script:InstallDir
        installed_at = (Get-Date).ToString("o")
        owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        app_home = $script:AppHome
        app_exe = $AppExe
        uninstaller = $script:UninstallerName
        installer = "nsis"
        estimated_size_kb = [int]$script:EstimatedSizeKb
        paths = [ordered]@{
            app_home = $script:AppHome
            local_app_data = $script:LocalAppData
        }
        options = [ordered]@{
            desktop_shortcut = ($DesktopShortcut -ne 0)
            start_menu_shortcut = ($StartMenuShortcut -ne 0)
            sandbox = $true
            show_sandbox_users = $false
        }
        sandbox = [ordered]@{
            requested = $true
            created = [bool]$script:SandboxCreated
            users = @("AlembicSbxOffline", "AlembicSbxOnline")
            proxy_port = 47831
        }
        shortcuts = @($script:CreatedShortcuts)
        registry = [ordered]@{
            uninstall_key = $script:UninstallKey
            app_path_key = $script:AppPathKey
        }
    }
    $json = $receipt | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText((Join-Path $script:InstallDir "install.json"), $json, $script:Utf8)
}

function Invoke-Rollback {
    foreach ($path in @($script:CreatedShortcuts)) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($script:RegistryTouched) {
        Remove-Item -LiteralPath $script:UninstallKey -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($script:AppPathTouched) {
        Remove-Item -LiteralPath $script:AppPathKey -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($script:SandboxCreated) {
        $setup = Join-Path $script:InstallDir "resources\install\setup-sandbox.ps1"
        if (Test-Path -LiteralPath $setup) {
            $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
            $previousHome = $env:ALEMBIC_HOME
            $env:ALEMBIC_HOME = $script:AppHome
            try {
                & $powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Uninstall 2>&1 |
                    ForEach-Object { Write-Log ("[rollback] " + [string]$_) }
            } finally {
                $env:ALEMBIC_HOME = $previousHome
            }
        }
    }
    if ($script:UninstallerCreated) {
        Remove-Item -LiteralPath (Join-Path $script:InstallDir $script:UninstallerName) -Force -ErrorAction SilentlyContinue
    }
}

try {
    Assert-Admin
    if (-not (Test-Path -LiteralPath (Join-Path $script:InstallDir $AppExe))) {
        throw "Missing application executable: $AppExe"
    }

    Write-Log "Verifying bundled runtimes"
    Assert-Runtime

    Write-Log "Configuring Windows command sandbox"
    Install-Sandbox

    $exe = Join-Path $script:InstallDir $AppExe
    $uninstaller = Join-Path $script:InstallDir $script:UninstallerName
    Copy-Item -LiteralPath $exe -Destination $uninstaller -Force
    $script:UninstallerCreated = $true

    Write-Log "Creating shortcuts"
    Install-Shortcuts

    Write-Log "Writing uninstall registration"
    Write-UninstallRegistry

    Write-Log "Writing install receipt"
    Write-Receipt

    Write-Output "OK"
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Output "ERROR: $message"
    try { Invoke-Rollback } catch { Write-Log ("Rollback failed: " + $_.Exception.Message) }
    exit 1
}
