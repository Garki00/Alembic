#Requires -Version 5.1
<#
.SYNOPSIS
    Build an ALEMBIC Windows release: frontend, Python sidecar, Electron app,
    NSIS installer, checksums and release metadata.

.DESCRIPTION
    Product identity and installer options come from packaging/release.config.json;
    desktop/package.json must describe the same application and is validated before
    any build step runs. The unpacked Electron application is built first and then
    packaged with electron-builder's prepackaged mode, so the installer and the
    application always use the same files.

    Use -Dev to build a developer package. The developer marker is only added to
    the output/product names; the application UI never shows a release label.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$Dev,
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$SkipApp,
    [switch]$SkipInstaller,
    [switch]$Clean,
    [switch]$Plan
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$configPath = Join-Path $root "packaging\release.config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Missing release configuration: $configPath"
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json

foreach ($required in @("product", "appId", "publisher", "version")) {
    if (-not [string]$config.$required) {
        throw "release.config.json is missing '$required'."
    }
}
$product = [string]$config.product
$appId = [string]$config.appId
$publisher = [string]$config.publisher
$minimumWindows = [string]$config.minimumWindows
$arch = if ([string]$config.arch) { [string]$config.arch } else { "x64" }
if ($arch -ne "x64") {
    throw "release.config.json arch must be x64; the bundled runtimes and NSIS flow target win32-x64."
}

$requestedVersion = $Version
if (-not $Version) { $Version = [string]$config.version }
if (-not $Version) {
    throw "release.config.json has no version; pass -Version."
}
if ($requestedVersion -and $requestedVersion -ne [string]$config.version) {
    $config.version = $requestedVersion
    $json = $config | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding($false)))
}

$productName = if ($Dev) { "$product-开发版" } else { $product }
$exeName = $productName
$channel = if ($Dev) { "dev" } else { [string]$config.channel }
$artifactName = "$productName-Setup-${Version}-${arch}.exe"

$desktopDir = Join-Path $root "desktop"
$frontendDir = Join-Path $root "frontend"
$releaseRoot = Join-Path $root "build\release"
$outDir = Join-Path $releaseRoot "out"
$unpacked = Join-Path $desktopDir "dist\win-unpacked"
$nsisAssets = Join-Path $desktopDir "build\nsis"

function Write-Phase {
    param([string]$Title)
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
}

function Assert-File {
    param([string]$Path, [string]$Hint)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing file: $Path`n$Hint"
    }
}

function Remove-DirectorySafe {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

# 安装器用 Windows PowerShell 5.1（powershell.exe）运行这些脚本。5.1 对没有 BOM
# 的 UTF-8 文件按当前 ANSI 代码页解析，脚本里的中文会让字符串提前截断，卸载 worker
# 直接语法错误，界面只能显示「卸载未完成」。这些文件会原样复制进安装包，所以在构建
# 阶段就校验 BOM，避免把无法解析的脚本发出去。
function Assert-Utf8Bom {
    param([string]$Path)
    Assert-File $Path "Required PowerShell script is missing."
    $head = [IO.File]::ReadAllBytes($Path)
    if ($head.Length -lt 3 -or $head[0] -ne 0xEF -or $head[1] -ne 0xBB -or $head[2] -ne 0xBF) {
        throw "PowerShell 脚本缺少 UTF-8 BOM：$Path`nWindows PowerShell 5.1 会把无 BOM 的 UTF-8 当作 ANSI，中文会导致语法错误；请用「UTF-8 with BOM」保存。"
    }
}

function Get-CheckedCommand {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "Required command not found: $Name" }
    return $command.Source
}

# release.config.json is the single source for product identity and installer
# options; desktop/package.json must describe the same application, otherwise the
# unpacked directory and the NSIS installer disagree about names or settings.
function Assert-ReleaseConfig {
    $desktopPkgPath = Join-Path $desktopDir "package.json"
    Assert-File $desktopPkgPath "desktop/package.json is required to build the installer."
    $desktopPkg = Get-Content -LiteralPath $desktopPkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $build = $desktopPkg.build

    if ([string]$build.appId -ne $appId) {
        throw "desktop/package.json build.appId '$($build.appId)' does not match release.config.json appId '$appId'."
    }
    if ([string]$build.productName -ne $product) {
        throw "desktop/package.json build.productName '$($build.productName)' does not match release.config.json product '$product'."
    }
    if (-not $build.win -or [string]$build.win.executableName -ne $product) {
        throw "desktop/package.json build.win.executableName must be '$product' for release builds."
    }
    if ($config.installer.requireAdmin -eq $true -and [string]$build.win.requestedExecutionLevel -ne "requireAdministrator") {
        throw "release.config.json requires admin; desktop/package.json build.win.requestedExecutionLevel must be requireAdministrator."
    }
    if ($config.installer.perMachine -eq $true -and $build.nsis.perMachine -ne $true) {
        throw "release.config.json requires a perMachine installer; desktop/package.json build.nsis.perMachine must be true."
    }
    if ([bool]$build.nsis.allowToChangeInstallationDirectory -ne [bool]$config.installer.allowCustomDir) {
        throw "release.config.json installer.allowCustomDir and desktop/package.json build.nsis.allowToChangeInstallationDirectory disagree."
    }
    if ($config.installer.desktopShortcutByDefault -eq $false -or $config.installer.startMenuShortcutByDefault -eq $false) {
        throw "Shortcut defaults are fixed to selected in packaging/nsis/installer.nsh; change that page before changing release.config.json shortcuts."
    }
}

Assert-ReleaseConfig

# 随安装包分发、并由 powershell.exe 5.1 执行的脚本；缺 BOM 时中文会导致解析失败，
# 表现为安装后卸载界面报「卸载未完成」。
foreach ($relative in @(
    "scripts\setup-sandbox.ps1",
    "packaging\install\uninstall-worker.ps1",
    "packaging\install\nsis-postinstall.ps1"
)) {
    Assert-Utf8Bom (Join-Path $root $relative)
}

if ($Plan) {
    Write-Host "ALEMBIC installer build plan (version $Version, channel $channel)" -ForegroundColor Green
    Write-Host "  product: $productName"
    Write-Host "  app id: $appId"
    Write-Host "  publisher: $publisher"
    Write-Host "  executable: $exeName.exe"
    Write-Host "  installer: $artifactName"
    Write-Host "  arch: $arch; minimum Windows: $minimumWindows"
    Write-Host "  config checks: ok"
    Write-Host "  1. sync package versions"
    if (-not $SkipFrontend) { Write-Host "  2. frontend: npm run build" }
    if (-not $SkipBackend) { Write-Host "  3. backend: packaging\build.ps1 -Clean:$Clean" }
    if (-not $SkipApp) { Write-Host "  4. desktop: electron-builder --dir" }
    Write-Host "  5. generate NSIS bitmaps"
    if (-not $SkipInstaller) { Write-Host "  6. desktop: electron-builder --win nsis --prepackaged" }
    Write-Host "  7. output: $outDir\$artifactName"
    exit 0
}

$started = Get-Date

if ($Clean) {
    Remove-DirectorySafe (Join-Path $desktopDir "dist")
    Remove-DirectorySafe $outDir
    Remove-DirectorySafe $nsisAssets
}

# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------
Write-Phase "Sync versions"
$syncScript = Join-Path $root "packaging\tools\sync-version.mjs"
Assert-File $syncScript "Missing packaging/tools/sync-version.mjs."
$node = Get-CheckedCommand "node"
& $node $syncScript $Version `
    (Join-Path $desktopDir "package.json") `
    (Join-Path $desktopDir "package-lock.json") `
    (Join-Path $frontendDir "package.json") `
    (Join-Path $frontendDir "package-lock.json")
if ($LASTEXITCODE -ne 0) { throw "Version sync failed." }

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Phase "Build frontend"
    $npm = Get-CheckedCommand "npm"
    Assert-File (Join-Path $frontendDir "node_modules") "Run cd frontend && npm install first."
    Push-Location $frontendDir
    try {
        $env:npm_config_cache = Join-Path $root ".tmp\npm-cache"
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    } finally {
        Pop-Location
    }
} else {
    Write-Phase "Skip frontend build"
}
Assert-File (Join-Path $frontendDir "dist\index.html") "Run cd frontend && npm run build first."

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
if (-not $SkipBackend) {
    Write-Phase "Build Python backend"
    $backendScript = Join-Path $root "packaging\build.ps1"
    Assert-File $backendScript "Missing packaging/build.ps1."
    $backendArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backendScript)
    if ($Clean) { $backendArgs += "-Clean" }
    & powershell @backendArgs
    if ($LASTEXITCODE -ne 0) { throw "Backend build failed." }
} else {
    Write-Phase "Skip backend build"
}
$backendExe = Join-Path $root "dist\alembic-backend\alembic-backend.exe"
Assert-File $backendExe "Run packaging\build.ps1 first."
Assert-File (Join-Path $root "dist\alembic-backend\runtimes\win32-x64\installed.json") `
    "Run scripts\provision_runtimes.py first."

# ---------------------------------------------------------------------------
# Electron unpacked app
# ---------------------------------------------------------------------------
if (-not $SkipApp) {
    Write-Phase "Build Electron app directory"
    $npm = Get-CheckedCommand "npm"
    Assert-File (Join-Path $desktopDir "node_modules\.bin\electron-builder.cmd") `
        "Run cd desktop && npm install first."
    Push-Location $desktopDir
    try {
        $builderArgs = @("run", "build:app", "--")
        if ($Dev) {
            $builderArgs += "-c.productName=$productName"
            $builderArgs += "-c.win.executableName=$exeName"
        }
        & $npm @builderArgs
        if ($LASTEXITCODE -ne 0) { throw "Electron app build failed." }
    } finally {
        Pop-Location
    }
} else {
    Write-Phase "Skip Electron app directory build"
}
$appExe = Join-Path $unpacked "$exeName.exe"
Assert-File $appExe "Electron app directory does not contain $exeName.exe."

foreach ($relative in @(
    "resources\app.asar",
    "resources\frontend\index.html",
    "resources\backend\alembic-backend.exe",
    "resources\backend\runtimes\win32-x64\installed.json",
    "resources\install\setup-sandbox.ps1",
    "resources\install\uninstall-worker.ps1",
    "resources\install\nsis-postinstall.ps1"
)) {
    Assert-File (Join-Path $unpacked $relative) "Electron app directory is incomplete."
}

if ($SkipInstaller) {
    Write-Phase "Skip NSIS installer"
    Write-Host "Unpacked application: $unpacked"
    Write-Host ("Finished in {0:N1} minutes." -f ((Get-Date) - $started).TotalMinutes) -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# NSIS assets and installer
# ---------------------------------------------------------------------------
Write-Phase "Generate NSIS assets"
$assetScript = Join-Path $root "packaging\tools\make-nsis-assets.ps1"
Assert-File $assetScript "Missing packaging/tools/make-nsis-assets.ps1."
& powershell -NoProfile -ExecutionPolicy Bypass -File $assetScript -OutputDir $nsisAssets
if ($LASTEXITCODE -ne 0) { throw "NSIS asset generation failed." }

Write-Phase "Build NSIS installer"
$npm = Get-CheckedCommand "npm"
Push-Location $desktopDir
try {
    $installerArgs = @("run", "build:nsis", "--")
    # 由 release.config.json 派生的产品名统一决定安装包文件名，避免与 -Dev 的
    # 命名规则或 desktop/package.json 的静态 artifactName 漂移。
    $installerArgs += "-c.nsis.artifactName=$productName-Setup-`${version}-`${arch}.`${ext}"
    if ($Dev) {
        $installerArgs += "-c.productName=$productName"
        $installerArgs += "-c.win.executableName=$exeName"
    }
    & $npm @installerArgs
    if ($LASTEXITCODE -ne 0) { throw "NSIS installer build failed." }
} finally {
    Pop-Location
}

$built = Join-Path $desktopDir "dist\$artifactName"
if (-not (Test-Path -LiteralPath $built)) {
    $candidate = Get-ChildItem -LiteralPath (Join-Path $desktopDir "dist") -Filter "*.exe" -File |
        Where-Object { $_.Name -like "*Setup*" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($candidate) { $built = $candidate.FullName }
}
Assert-File $built "NSIS installer was not produced."

if (-not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}
$setup = Join-Path $outDir $artifactName
Copy-Item -LiteralPath $built -Destination $setup -Force

$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $setup).Length
$utf8 = New-Object System.Text.UTF8Encoding($false)

$release = [ordered]@{
    schema = 2
    product = $product
    app_id = $appId
    publisher = $publisher
    version = $Version
    channel = $channel
    arch = $arch
    minimum_windows = $minimumWindows
    built_at = (Get-Date).ToString("o")
    commit = ""
    executable = "$exeName.exe"
    installer = $artifactName
    installer_sha256 = $hash
    installer_size = $size
    artifacts = @(
        [ordered]@{
            role = "installer"
            name = $artifactName
            sha256 = $hash
            size = $size
        }
    )
}
try {
    $commit = (& git rev-parse --short HEAD 2>$null | Select-Object -First 1).Trim()
    if ($commit) { $release.commit = $commit }
} catch { }
[IO.File]::WriteAllText(
    (Join-Path $outDir "release.json"),
    ($release | ConvertTo-Json -Depth 5),
    $utf8
)

[IO.File]::WriteAllText(
    (Join-Path $outDir "SHA256SUMS.txt"),
    ("{0}  {1}`n" -f $hash, $artifactName),
    $utf8
)

Write-Host ""
Write-Host "Installer: $setup" -ForegroundColor Green
Write-Host ("Size: {0:N1} MB" -f ($size / 1MB))
Write-Host "Checksums: $(Join-Path $outDir 'SHA256SUMS.txt')"
Write-Host ("Finished in {0:N1} minutes." -f ((Get-Date) - $started).TotalMinutes) -ForegroundColor Green
