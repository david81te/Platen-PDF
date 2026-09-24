# Builds PDF Studio.
#
#   .uild.ps1            fast folder build  -> dist\PDFStudio\PDFStudio.exe
#   .uild.ps1 -Portable  single file        -> dist\PDFStudio.exe
#
# The folder build starts in about a second and is the one to use when PDF
# Studio is your default PDF application; the single file is easier to copy
# around but unpacks itself on every launch.
param([switch]$Portable)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Portable) {
    Write-Host "Building the single-file build..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" --noconfirm --clean PDFStudio.spec
    $out = "dist\PDFStudio.exe"
} else {
    Write-Host "Building the folder build..." -ForegroundColor Cyan
    & ".venv\Scripts\pyinstaller.exe" --noconfirm --clean PDFStudio-folder.spec
    $out = "dist\PDFStudio\PDFStudio.exe"
}

if (Test-Path $out) {
    $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host "Built $out ($mb MB)" -ForegroundColor Green
    & $out --selftest
} else {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}
