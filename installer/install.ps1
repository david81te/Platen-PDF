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

# One spelling of the folder, used for it and for .new and .old beside it. A
# trailing backslash put .new inside the folder being replaced; a short 8.3
# name (PLATEN~1) moved the real folder aside and built one called that.
function Expand-Path([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    $root = [IO.Path]::GetPathRoot($full)
    $now = $root
    foreach ($part in $full.Substring($root.Length).Split([char[]]'\', [StringSplitOptions]::RemoveEmptyEntries)) {
        $found = $null
        if (Test-Path -LiteralPath $now) {
            # A filter matches short names too, and hands back the long one.
            $found = Get-ChildItem -LiteralPath $now -Filter $part -Force -ErrorAction SilentlyContinue |
                Select-Object -First 1
        }
        $now = Join-Path $now $(if ($found) { $found.Name } else { $part })
    }
    return $now.TrimEnd('\')
}
$Destination = Expand-Path $Destination

# The program writes to its error stream now and then - a library's warning.
# Under 'Stop', or under the update helper's *> capture, PowerShell turns any
# such line into a failure, and an update that had worked was reported broken.
function Invoke-Program([string]$exe, [string[]]$arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        return (& $exe @arguments 2>&1 | ForEach-Object { "$_" } | Out-String)
    } catch {
        return "$_"
    } finally {
        $ErrorActionPreference = $saved
    }
}

# Renaming a folder fails while anything has a file in it open, and right
# after the old copy exits or the new one is checked something often still
# does for a moment - an antivirus scan, the search indexer.
function Rename-Patiently([string]$path, [string]$name) {
    for ($try = 1; $try -le 20; $try++) {
        try {
            Rename-Item -LiteralPath $path -NewName $name -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    return $false
}

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
# Matched on the whole folder name: "Platen PDF 2" is not "Platen PDF".
$running = Get-Process -Name 'PlatenPDF' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Destination + '\', [StringComparison]::OrdinalIgnoreCase) }
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
# A swap cut short - by a power cut, say - can leave only the old copy, set
# aside. It is the working program, so it goes back before anything is
# cleared away.
if (-not (Test-Path -LiteralPath $Destination) -and (Test-Path -LiteralPath (Join-Path $retired 'PlatenPDF.exe'))) {
    if (-not (Rename-Patiently $retired (Split-Path $Destination -Leaf))) {
        Write-Host "  The previous copy, at $retired, could not be put back. Nothing was changed." -ForegroundColor Red
        exit 1
    }
}
foreach ($stale in @($staged, $retired)) {
    if (Test-Path -LiteralPath $stale) {
        Remove-Item -LiteralPath $stale -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $stale) {
            Write-Host "  $stale is left over from an earlier install and could not be removed." -ForegroundColor Red
            Write-Host "  Restart the PC, delete it, and run this again. Nothing was changed."
            if (-not $Quiet) { Read-Host "  Press Enter to close" | Out-Null }
            exit 1
        }
    }
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
$check = Invoke-Program (Join-Path $staged 'PlatenPDF.exe') @('--selftest')
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
    if (-not (Rename-Patiently $Destination (Split-Path $retired -Leaf))) {
        Abandon @("The previous version is in use, so nothing was changed.",
                  "Close Platen PDF, and any window open in its folder, then try again.")
    }
}
if (-not (Rename-Patiently $staged $leaf)) {
    if ((Test-Path -LiteralPath $retired) -and -not (Rename-Patiently $retired $leaf)) {
        # Left as it is, the next run of this installer puts it back.
        Write-Host "  The previous version is at $retired. Run this installer again to restore it." -ForegroundColor Red
        exit 1
    }
    Abandon @("The new version could not be put in place, so the previous one was kept.")
}
if (Test-Path -LiteralPath $retired) {
    Remove-Item -LiteralPath $retired -Recurse -Force -ErrorAction SilentlyContinue
}

$exe = Join-Path $Destination 'PlatenPDF.exe'

# Leave an uninstaller behind so this is reversible without hunting for files.
# Paths go in single quotes, where an apostrophe - O'Brien's profile folder -
# has to be doubled or the whole script fails to parse.
function Quoted([string]$text) { "'" + $text.Replace("'", "''") + "'" }
$uninstallPs1 = Join-Path $Destination 'uninstall.ps1'
$startLink = Quoted (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk")
$deskLink = Quoted (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk")
# The remover runs as a command line inside this script's text, so the
# folder is quoted once for that command and once more for this script.
$remover = Quoted ("Start-Sleep -Seconds 2; Remove-Item -LiteralPath " + (Quoted $Destination) + " -Recurse -Force")
@"
`$ErrorActionPreference = 'SilentlyContinue'
Get-Process -Name 'PlatenPDF' | Where-Object { `$_.Path -and `$_.Path.StartsWith($(Quoted ($Destination + '\')), [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force
Start-Sleep -Seconds 1
& $(Quoted $exe) --unregister
Remove-Item -LiteralPath $startLink
Remove-Item -LiteralPath $deskLink
# The folder cannot delete itself while this script is running out of it, so
# hand the job to a second process that waits for this one to exit.
Start-Process powershell -ArgumentList '-NoProfile','-Command',$remover
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
    # Its answer does not decide the install: the new copy is already in place.
    if ($Quiet) { $null = Invoke-Program $exe @('--register', '--quiet') }
    else { $null = Invoke-Program $exe @('--register') }
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
