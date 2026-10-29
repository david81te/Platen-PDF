# Builds Platen PDF.
#
#   .\build.ps1            fast folder build  -> dist\PlatenPDF\PlatenPDF.exe
#   .\build.ps1 -Portable  single file        -> dist\PlatenPDF.exe
#
# The folder build starts in about a second and is the one to use when Platen
# PDF is your default PDF application; the single file is easier to copy
# around but unpacks itself on every launch.
param([switch]$Portable)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# The repo sits in OneDrive, which turns a folder it has synced into a cloud
# placeholder, and PyInstaller cannot delete one: the second build on a
# machine failed with "Access is denied", first on build\...\localpycs and
# then on dist\PlatenPDF. Remove-Item can, so the previous output is cleared
# here, and PyInstaller's scratch folder lives outside the repo altogether.
$work = Join-Path $env:LOCALAPPDATA "PlatenPDF-build"
$old = if ($Portable) { "dist\PlatenPDF.exe" } else { "dist\PlatenPDF" }
if (Test-Path $old) { Remove-Item -LiteralPath $old -Recurse -Force }

if ($Portable) {
    Write-Host "Building the single-file build..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --workpath $work PlatenPDF.spec
    $out = "dist\PlatenPDF.exe"
} else {
    Write-Host "Building the folder build..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --workpath $work PlatenPDF-folder.spec
    $out = "dist\PlatenPDF\PlatenPDF.exe"
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller failed (exit $LASTEXITCODE). The previous build was cleared first, so there is none in dist\." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $out)) {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "Built $out ($mb MB)" -ForegroundColor Green

# The exe is windowed, so calling it plainly returns at once and leaves
# $LASTEXITCODE untouched - a check written that way passes no matter what.
# Piping the output is what makes PowerShell wait for it and capture the result.
$log = (& $out --selftest 2>&1 | Out-String).Trim()
Write-Host $log
if ($log -notmatch 'SELFTEST: PASS') {
    Write-Host "The build did not pass its self-test." -ForegroundColor Red
    exit 1
}
