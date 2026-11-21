#Requires -Version 5.1
<#
.SYNOPSIS
    Generate the NSIS wizard bitmaps from the ALEMBIC palette.

.DESCRIPTION
    NSIS cannot render SVG or HTML, so the installer images are drawn here with
    System.Drawing during the release build. The generated files are build
    artifacts and are not committed.
#>
param(
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$paper = [System.Drawing.ColorTranslator]::FromHtml("#faf8f1")
$paperDeep = [System.Drawing.ColorTranslator]::FromHtml("#f4f2e8")
$card = [System.Drawing.ColorTranslator]::FromHtml("#fffdf8")
$emerald = [System.Drawing.ColorTranslator]::FromHtml("#397157")
$mint = [System.Drawing.ColorTranslator]::FromHtml("#d2ddd6")
$border = [System.Drawing.ColorTranslator]::FromHtml("#dedfd4")

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

function New-Canvas {
    param([int]$Width, [int]$Height, [System.Drawing.Color]$Fill)
    $bitmap = New-Object System.Drawing.Bitmap($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear($Fill)
    return @{ Bitmap = $bitmap; Graphics = $graphics }
}

function Draw-Sun {
    param(
        [System.Drawing.Graphics]$Graphics,
        [int]$CenterX,
        [int]$CenterY,
        [int]$Radius,
        [int]$Stroke
    )
    $pen = New-Object System.Drawing.Pen($emerald, $Stroke)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    try {
        $diameter = $Radius * 2
        $Graphics.DrawEllipse($pen, $CenterX - $Radius, $CenterY - $Radius, $diameter, $diameter)
        for ($index = 0; $index -lt 8; $index++) {
            $angle = ($index * 45) * [Math]::PI / 180
            $inner = $Radius + [int]($Radius * 0.48)
            $outer = $Radius + [int]($Radius * 0.95)
            $x1 = $CenterX + [int]([Math]::Cos($angle) * $inner)
            $y1 = $CenterY + [int]([Math]::Sin($angle) * $inner)
            $x2 = $CenterX + [int]([Math]::Cos($angle) * $outer)
            $y2 = $CenterY + [int]([Math]::Sin($angle) * $outer)
            $Graphics.DrawLine($pen, $x1, $y1, $x2, $y2)
        }
    } finally {
        $pen.Dispose()
    }
}

function Save-Bitmap {
    param($Canvas, [string]$Path)
    try {
        $Canvas.Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Bmp)
    } finally {
        $Canvas.Graphics.Dispose()
        $Canvas.Bitmap.Dispose()
    }
}

# Drawn titlebar for the frameless options page.
$titlebar = New-Canvas -Width 500 -Height 40 -Fill $paperDeep
$titlebarBorder = New-Object System.Drawing.Pen($border, 1)
$titlebarAccent = New-Object System.Drawing.Pen($emerald, 3)
try {
    $titlebar.Graphics.DrawLine($titlebarBorder, 0, 39, 500, 39)
    $titlebar.Graphics.DrawLine($titlebarAccent, 0, 0, 0, 40)
} finally {
    $titlebarBorder.Dispose()
    $titlebarAccent.Dispose()
}
Save-Bitmap -Canvas $titlebar -Path (Join-Path $OutputDir "installerTitlebar.bmp")

# Full-page background for the custom options page.
$page = New-Canvas -Width 500 -Height 360 -Fill $paper
$page.Graphics.DrawLine((New-Object System.Drawing.Pen($border, 1)), 0, 359, 500, 359)
$line = New-Object System.Drawing.Pen($emerald, 2)
try {
    $page.Graphics.DrawLine($line, 18, 22, 18, 328)
} finally {
    $line.Dispose()
}
Draw-Sun -Graphics $page.Graphics -CenterX 442 -CenterY 34 -Radius 9 -Stroke 2
Save-Bitmap -Canvas $page -Path (Join-Path $OutputDir "page.bmp")

# MUI header image (150 x 57).
$header = New-Canvas -Width 150 -Height 57 -Fill $card
Draw-Sun -Graphics $header.Graphics -CenterX 22 -CenterY 29 -Radius 10 -Stroke 2
$headerLine = New-Object System.Drawing.Pen($border, 1)
try {
    $header.Graphics.DrawLine($headerLine, 42, 16, 138, 16)
    $header.Graphics.DrawLine($headerLine, 42, 22, 118, 22)
} finally {
    $headerLine.Dispose()
}
Save-Bitmap -Canvas $header -Path (Join-Path $OutputDir "installerHeader.bmp")

# MUI welcome/finish sidebar (164 x 314).
function New-Sidebar {
    $canvas = New-Canvas -Width 164 -Height 314 -Fill $paperDeep
    Draw-Sun -Graphics $canvas.Graphics -CenterX 82 -CenterY 72 -Radius 30 -Stroke 3
    $mintPen = New-Object System.Drawing.Pen($mint, 2)
    try {
        $canvas.Graphics.DrawLine($mintPen, 24, 126, 140, 126)
        $canvas.Graphics.DrawLine($mintPen, 24, 134, 112, 134)
    } finally {
        $mintPen.Dispose()
    }
    return $canvas
}
Save-Bitmap -Canvas (New-Sidebar) -Path (Join-Path $OutputDir "installerSidebar.bmp")
Save-Bitmap -Canvas (New-Sidebar) -Path (Join-Path $OutputDir "uninstallerSidebar.bmp")

Write-Host "NSIS assets generated in $OutputDir"

# Electron Builder's assisted installer is a 32-bit Unicode NSIS process. Build
# its UI bridge as a native x86 DLL; do not substitute an Electron/.NET fallback.
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw "Visual Studio Installer (vswhere.exe) is required to build the native NSIS UI. Install the Visual C++ x86/x64 build tools."
}
$vsInstall = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $vsInstall) {
    throw "Visual C++ x86/x64 build tools are required to build the native NSIS UI."
}
$vsDevCmd = Join-Path $vsInstall "Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path -LiteralPath $vsDevCmd)) {
    throw "Visual Studio developer environment was not found: $vsDevCmd"
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$nativeSource = Join-Path $repoRoot "packaging\installer-ui\alembic-installer-ui.cpp"
$nativeExports = Join-Path $repoRoot "packaging\installer-ui\alembic-installer-ui.def"
if (-not (Test-Path -LiteralPath $nativeSource)) {
    throw "Missing native NSIS UI source: $nativeSource"
}
if (-not (Test-Path -LiteralPath $nativeExports)) {
    throw "Missing native NSIS UI export list: $nativeExports"
}
if ([IO.Path]::IsPathRooted($OutputDir)) {
    $nsisOutput = $OutputDir
} else {
    $nsisOutput = Join-Path (Get-Location) $OutputDir
}
$pluginDir = Join-Path $nsisOutput "x86-unicode"
New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
$nativeDll = Join-Path $pluginDir "alembic-installer-ui.dll"
$nativeObject = Join-Path $pluginDir "alembic-installer-ui.obj"
$buildCommand = 'call "{0}" -no_logo -arch=x86 -host_arch=x64 && cl.exe /nologo /std:c++17 /EHsc /O2 /MT /DUNICODE /D_UNICODE /c "{1}" /Fo"{2}" && link.exe /nologo /DLL /MACHINE:X86 /DEF:"{3}" /OUT:"{4}" "{2}" gdiplus.lib comctl32.lib dwmapi.lib ole32.lib shell32.lib advapi32.lib user32.lib gdi32.lib uuid.lib' -f $vsDevCmd, $nativeSource, $nativeObject, $nativeExports, $nativeDll
$cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
& $cmd /d /c $buildCommand
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nativeDll)) {
    throw "Native x86 NSIS UI compilation failed."
}
Write-Host "Native x86 Unicode NSIS UI compiled: $nativeDll"
