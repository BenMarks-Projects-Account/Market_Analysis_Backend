<#
.SYNOPSIS
    Rebuild the BenTrade Launcher EXE from source.
.DESCRIPTION
    Cleans old PyInstaller artifacts, rebuilds using the canonical .spec file,
    and optionally launches the result.
.PARAMETER Launch
    Auto-launch the built EXE after a successful build.
#>
param(
    [switch]$Launch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Resolve paths --
$repoRoot   = (Resolve-Path "$PSScriptRoot\..").Path
$specFile   = Join-Path $repoRoot 'BenTrade\backend\launcher.spec'
$buildDir   = Join-Path $repoRoot 'build\launcher'
$distDir    = Join-Path $repoRoot 'dist\launcher'
$exePath    = Join-Path $distDir  'launcher.exe'

Write-Host "`n=== BenTrade Launcher Rebuild ===" -ForegroundColor Cyan
Write-Host "Repo root : $repoRoot"
Write-Host "Spec file : $specFile"

if (-not (Test-Path $specFile)) {
    Write-Error "Spec file not found at $specFile - aborting."
    exit 1
}

# -- Clean old artifacts --
Write-Host "`nCleaning old build artifacts..." -ForegroundColor Yellow
foreach ($dir in @($buildDir, $distDir)) {
    if (Test-Path $dir) {
        Remove-Item -Recurse -Force $dir
        Write-Host "  Removed $dir"
    }
}

# -- Activate venv if present --
$venvActivate = Join-Path $repoRoot '.venv\Scripts\Activate.ps1'
if (Test-Path $venvActivate) {
    Write-Host "`nActivating virtualenv..." -ForegroundColor Yellow
    & $venvActivate
}

# -- Verify PyInstaller is available --
$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Error "pyinstaller not found on PATH. Install with: pip install pyinstaller"
    exit 1
}
Write-Host "PyInstaller: $($pyinstaller.Source)"

# -- Build --
Write-Host "`nBuilding..." -ForegroundColor Green
Push-Location $repoRoot
try {
    & pyinstaller --noconfirm --clean $specFile
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller exited with code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

# -- Verify output --
if (-not (Test-Path $exePath)) {
    Write-Error "Build succeeded but EXE not found at $exePath"
    exit 1
}

$info = Get-Item $exePath
Write-Host "`n=== Build Complete ===" -ForegroundColor Cyan
Write-Host "EXE       : $($info.FullName)"
Write-Host "Size      : $([math]::Round($info.Length / 1MB, 2)) MB"
Write-Host "Timestamp : $($info.LastWriteTime)"

# -- Optional launch --
if ($Launch) {
    Write-Host "`nLaunching..." -ForegroundColor Green
    Start-Process $exePath
}
