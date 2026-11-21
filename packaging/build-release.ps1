#Requires -Version 5.1
<#
.SYNOPSIS
    Build an ALEMBIC Windows release: frontend, Python sidecar, Electron app,
    themed NSIS installer, checksums.

.DESCRIPTION
    The installer is native NSIS. The unpacked Electron application is built
    first and then packaged with electron-builder's prepackaged mode, so the
    installer and the application always use the same files.

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

$productName = if ($Dev) { "ALEMBIC-开发版" } else { "ALEMBIC" }
$exeName = if ($Dev) { "ALEMBIC-开发版" } else { "ALEMBIC" }
$channel = if ($Dev) { "dev" } else { [string]$config.channel }
$artifactName = if ($Dev) {
    "ALEMBIC-开发版-Setup-${Version}-x64.exe"
} else {
    "ALEMBIC-Setup-${Version}-x64.exe"
}

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

function Get-CheckedCommand {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) { throw "Required command not found: $Name" }
    return $command.Source
}

if ($Plan) {
    Write-Host "ALEMBIC installer build plan (version $Version, channel $channel)" -ForegroundColor Green
    Write-Host "  product: $productName"
    Write-Host "  executable: $exeName.exe"
    Write-Host "  installer: $artifactName"
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
    if ($Dev) {
        $installerArgs += "-c.productName=$productName"
        $installerArgs += "-c.win.executableName=$exeName"
        $installerArgs += "-c.nsis.artifactName=ALEMBIC-开发版-Setup-`${version}-`${arch}.`${ext}"
    } else {
        $installerArgs += "-c.nsis.artifactName=ALEMBIC-Setup-`${version}-`${arch}.`${ext}"
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

$release = [ordered]@{
    schema = 1
    product = "ALEMBIC"
    version = $Version
    channel = $channel
    built_at = (Get-Date).ToString("o")
    commit = ""
    executable = "$exeName.exe"
    installer = $artifactName
}
try {
    $commit = (& git rev-parse --short HEAD 2>$null | Select-Object -First 1).Trim()
    if ($commit) { $release.commit = $commit }
} catch { }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    (Join-Path $outDir "release.json"),
    ($release | ConvertTo-Json -Depth 5),
    $utf8
)

$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(
    (Join-Path $outDir "SHA256SUMS.txt"),
    ("{0}  {1}`n" -f $hash, $artifactName),
    $utf8
)

Write-Host ""
Write-Host "Installer: $setup" -ForegroundColor Green
Write-Host ("Size: {0:N1} MB" -f ((Get-Item -LiteralPath $setup).Length / 1MB))
Write-Host "Checksums: $(Join-Path $outDir 'SHA256SUMS.txt')"
Write-Host ("Finished in {0:N1} minutes." -f ((Get-Date) - $started).TotalMinutes) -ForegroundColor Green
