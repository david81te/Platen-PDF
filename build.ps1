# Builds dist\PDFStudio.exe -- a single self-contained Windows executable.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "Building PDF Studio..." -ForegroundColor Cyan
& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean PDFStudio.spec
if (Test-Path "dist\PDFStudio.exe") {
    $mb = [math]::Round((Get-Item "dist\PDFStudio.exe").Length / 1MB, 1)
    Write-Host "Built dist\PDFStudio.exe ($mb MB)" -ForegroundColor Green
} else {
    Write-Host "Build failed." -ForegroundColor Red; exit 1
}
