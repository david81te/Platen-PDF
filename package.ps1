# Builds the file you hand to a teammate: dist\PDFStudio-Setup.zip
#
# The zip holds the application folder plus a double-click installer, so the
# instructions are "extract, run Install PDF Studio.cmd" and nothing else.
# Run build.ps1 first; this only packages what is already in dist\PDFStudio.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$app = "dist\PDFStudio"
if (-not (Test-Path "$app\PDFStudio.exe")) {
    Write-Host "dist\PDFStudio\PDFStudio.exe is missing. Run .\build.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Checking the build before packaging it..." -ForegroundColor Cyan
# The exe is windowed; piping is what makes PowerShell wait for it and see
# the result. Calling it plainly returns at once and checks nothing.
$log = (& "$app\PDFStudio.exe" --selftest 2>&1 | Out-String).Trim()
Write-Host $log
if ($log -notmatch 'SELFTEST: PASS') {
    Write-Host "The build failed its self-test. Not packaging a broken exe." -ForegroundColor Red
    exit 1
}

$stage = "dist\_stage"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null

Copy-Item -LiteralPath $app -Destination "$stage\PDFStudio" -Recurse
Copy-Item -LiteralPath "installer\Install PDF Studio.cmd" -Destination $stage
Copy-Item -LiteralPath "installer\install.ps1" -Destination $stage

@"
PDF Studio
==========

1. Extract this zip somewhere first - Downloads or the desktop is fine.
   Right-click the zip, Extract All. Do not run anything from inside the
   zip itself; Windows only unpacks one file at a time from there and the
   installer will not find the program.

2. Open the extracted folder and double-click "Install PDF Studio".

3. Windows will say the publisher is unknown. Choose More info, then
   Run anyway. It says that about anything not signed by a company with a
   paid certificate; it is not a virus warning.

That is it. PDF Studio appears in the Start menu and on the desktop.
Nothing else needs downloading - the whole program, including the text
recogniser for scanned documents, is inside the folder.

It installs only for you, under your own user profile, so it never asks
for an administrator password.

To make it open PDFs when you double-click them, start PDF Studio and use
File > Set as default PDF app.

To remove it, open the folder it installed to:
  %LOCALAPPDATA%\Programs\PDF Studio
and run "Uninstall PDF Studio".

Questions: David Willmore.
"@ | Set-Content -LiteralPath "$stage\Read me first.txt" -Encoding ASCII

$zip = "dist\PDFStudio-Setup.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Write-Host "Compressing..." -ForegroundColor Cyan

# The entries are written by hand because every built-in route on Windows
# PowerShell 5.1 - Compress-Archive and ZipFile::CreateFromDirectory alike -
# puts backslashes in the entry names. Explorer copes; the zip spec says
# forward slashes, and anything else unpacking this deserves a valid file.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sep = [string][char]92
$root = (Resolve-Path $stage).Path.TrimEnd($sep)
$archive = [IO.Compression.ZipFile]::Open((Join-Path $PSScriptRoot $zip), 'Create')
try {
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
        $name = $file.FullName.Substring($root.Length + 1).Replace($sep, '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $name,
            [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {
    $archive.Dispose()
}

Remove-Item -LiteralPath $stage -Recurse -Force

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Built $zip ($mb MB)" -ForegroundColor Green
Write-Host "Send that one file. The person extracts it and runs Install PDF Studio."
