# Installs Platen PDF for the person running it. No administrator rights are
# needed and nothing outside the user profile is touched, so it works on a
# locked-down machine and can be undone with Uninstall Platen PDF.cmd.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\Platen PDF'),
    [switch]$NoShortcuts,
    [switch]$NoAssociation,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$AppName = 'Platen PDF'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say([string]$text, [string]$colour = 'Gray') {
    if (-not $Quiet) { Write-Host $text -ForegroundColor $colour }
}

# The payload sits either beside this script or one level up, depending on how
# the zip was unpacked.
$payload = $null
foreach ($candidate in @((Join-Path $here 'PlatenPDF'), $here, (Split-Path $here -Parent))) {
    if ($candidate -and (Test-Path (Join-Path $candidate 'PlatenPDF.exe'))) {
        $payload = $candidate
        break
    }
}
if (-not $payload) {
    Write-Host ""
    if ($here -like "$env:TEMP*" -or $here -match '\Temp\d*_') {
        # Double-clicking inside Explorer's zip preview copies out this one file
        # and nothing else, so there is no program here to install.
        Write-Host "  It looks like you ran this from inside the zip." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Close this, then right-click the zip in your Downloads folder and"
        Write-Host "  choose Extract All. Open the folder that appears and run"
        Write-Host "  Install Platen PDF from there."
    } else {
        Write-Host "  Could not find PlatenPDF.exe next to this file." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Install Platen PDF and the PlatenPDF folder have to stay together."
        Write-Host "  Extract the whole zip again and run it from there."
    }
    Write-Host ""
    if (-not $Quiet) { Read-Host "  Press Enter to close" | Out-Null }
    exit 1
}

Say ""
Say "  Installing $AppName" Cyan
Say "  from $payload"
Say "  to   $Destination"
Say ""

# Windows marks anything that arrived from the internet, and that mark makes
# every file in the folder prompt or silently fail. Clear it on our own files.
Say "  Clearing the downloaded-file mark..."
Get-ChildItem -LiteralPath $payload -Recurse -File -ErrorAction SilentlyContinue |
    ForEach-Object { Unblock-File -LiteralPath $_.FullName -ErrorAction SilentlyContinue }

# A running copy would lock the files we are about to replace. Run by hand,
# the installer closes it. Run quietly - which is how Platen PDF updates
# itself, after closing the window that asked - it refuses instead: any copy
# still running is another window, which may be holding unsaved work.
$running = Get-Process -Name 'PlatenPDF' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Destination, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    if ($Quiet) {
        Write-Host "  Platen PDF is still running, so nothing was changed."
        exit 1
    }
    Say "  Closing the copy that is already running..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# The new copy is built beside the old one and proven there, then swapped in.
# Deleting the old copy first meant anything that went wrong afterwards - a
# file held open, an antivirus scan, a full disk, a bad zip - left an empty
# folder and no working program. Now the old copy stays until the new one
# has passed its check, and comes back if the swap fails.
$staged = "$Destination.new"
$retired = "$Destination.old"
foreach ($stale in @($staged, $retired)) {
    if (Test-Path -LiteralPath $stale) { Remove-Item -LiteralPath $stale -Recurse -Force }
}
New-Item -ItemType Directory -Path $staged -Force | Out-Null

function Abandon([string[]]$lines) {
    if (Test-Path -LiteralPath $staged) {
        Remove-Item -LiteralPath $staged -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host ("  " + $lines[0]) -ForegroundColor Red
    foreach ($line in $lines[1..($lines.Count)]) { if ($line) { Write-Host ("  " + $line) } }
    Write-Host ""
    if (-not $Quiet) { Read-Host "  Press Enter to close" | Out-Null }
    exit 1
}

Say "  Copying files..."
Copy-Item -Path (Join-Path $payload '*') -Destination $staged -Recurse -Force
# The installer scripts themselves do not belong in the installed copy.
foreach ($leftover in @('Install Platen PDF.cmd', 'install.ps1', 'Read me first.txt')) {
    $stale = Join-Path $staged $leftover
    if (Test-Path $stale) { Remove-Item -LiteralPath $stale -Force }
}
if (-not (Test-Path (Join-Path $staged 'PlatenPDF.exe'))) {
    Abandon @("The copy did not produce PlatenPDF.exe. Nothing was changed.")
}

# Prove the new copy actually works before it replaces anything. An
# extraction that stopped part way through leaves the exe present but its
# libraries missing, and without this the installer would cheerfully finish
# and the failure would surface later as a program that will not start.
# Piping is what makes PowerShell wait: the exe is windowed, so calling it
# plainly returns at once and tests nothing.
Say "  Checking the new copy..."
$check = (& (Join-Path $staged 'PlatenPDF.exe') --selftest 2>&1 | Out-String)
if ($check -notmatch 'SELFTEST: PASS') {
    Abandon @("The new copy did not pass its own check. Nothing was changed.",
              "",
              "This usually means the zip was not fully extracted. Delete the",
              "extracted folder, unzip it again, and run this once more.",
              "Extracting somewhere short, like your Downloads folder, avoids",
              "the Windows path-length limit that causes it.")
}

$leaf = Split-Path $Destination -Leaf
if (Test-Path -LiteralPath $Destination) {
    Say "  Replacing the previous version..."
    try {
        Rename-Item -LiteralPath $Destination -NewName (Split-Path $retired -Leaf) -ErrorAction Stop
    } catch {
        Abandon @("The previous version is in use, so nothing was changed.",
                  "Close Platen PDF, and any window open in its folder, then try again.")
    }
}
try {
    Rename-Item -LiteralPath $staged -NewName $leaf -ErrorAction Stop
} catch {
    if (Test-Path -LiteralPath $retired) { Rename-Item -LiteralPath $retired -NewName $leaf }
    Abandon @("The new version could not be put in place, so the previous one was kept.")
}
if (Test-Path -LiteralPath $retired) {
    Remove-Item -LiteralPath $retired -Recurse -Force -ErrorAction SilentlyContinue
}

$exe = Join-Path $Destination 'PlatenPDF.exe'

# Leave an uninstaller behind so this is reversible without hunting for files.
$uninstallPs1 = Join-Path $Destination 'uninstall.ps1'
@"
`$ErrorActionPreference = 'SilentlyContinue'
Get-Process -Name 'PlatenPDF' | Stop-Process -Force
Start-Sleep -Seconds 1
& '$exe' --unregister
Remove-Item '$(Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk")'
Remove-Item '$(Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk")'
# The folder cannot delete itself while this script is running out of it, so
# hand the job to a second process that waits for this one to exit.
Start-Process powershell -ArgumentList '-NoProfile','-Command',"Start-Sleep -Seconds 2; Remove-Item -LiteralPath '$Destination' -Recurse -Force"
Write-Host 'Platen PDF has been removed.'
Start-Sleep -Seconds 2
"@ | Set-Content -LiteralPath $uninstallPs1 -Encoding UTF8

@"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
"@ | Set-Content -LiteralPath (Join-Path $Destination 'Uninstall Platen PDF.cmd') -Encoding ASCII

if (-not $NoShortcuts) {
    Say "  Adding shortcuts..."
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Programs'),
                          [Environment]::GetFolderPath('Desktop'))) {
        if (-not $folder) { continue }
        $link = $shell.CreateShortcut((Join-Path $folder "$AppName.lnk"))
        $link.TargetPath = $exe
        $link.WorkingDirectory = $Destination
        $link.IconLocation = "$exe,0"
        $link.Description = 'Edit, sign and convert PDF files'
        $link.Save()
    }
}

if (-not $NoAssociation) {
    # Puts Platen PDF in the "Open with" list. Windows will not let any
    # program make itself the default; that is a choice only you can make.
    Say "  Registering as a PDF application..."
    # Quiet is how Platen PDF updates itself: re-register, but leave Settings
    # closed for someone who has already chosen their PDF app.
    if ($Quiet) { & $exe --register --quiet | Out-Null } else { & $exe --register | Out-Null }
}

Say ""
Say "  Installed." Green
Say ""
Say "  Start it from the Start menu or the desktop shortcut."
Say "  The first launch warns that the publisher is unknown: choose"
Say "  More info, then Run anyway."
Say ""
Say "  To open PDFs with it by default, use File > Set as default PDF app"
Say "  inside the program."
Say ""
Say "  To remove it later, run Uninstall Platen PDF.cmd in:"
Say "  $Destination"
Say ""
if (-not $Quiet) { Read-Host "  Press Enter to close" | Out-Null }
exit 0
