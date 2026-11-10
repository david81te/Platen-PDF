"""The real installer, run the way an update runs it, into scratch folders.

The program it installs is a stand-in that answers --selftest and, like the
real one now and then, writes a line to its error stream. Nothing here makes
shortcuts or touches file associations.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(ROOT, "installer", "install.ps1")

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-64s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


scratch = tempfile.mkdtemp(prefix="platen-installer-")
STAND_IN = r"""
using System;
using System.IO;
class Program {
    static int Main(string[] args) {
        Console.Error.WriteLine("a library warning on the error stream");
        if (args.Length > 0 && args[0] == "--selftest") {
            string verdict = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "verdict.txt");
            Console.WriteLine(File.Exists(verdict) ? File.ReadAllText(verdict) : "SELFTEST: PASS");
        }
        return 0;
    }
}
"""
exe = os.path.join(scratch, "PlatenPDF.exe")
source = os.path.join(scratch, "standin.cs")
with open(source, "w", encoding="utf-8") as handle:
    handle.write(STAND_IN)
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath '%s') "
                "-OutputType ConsoleApplication -OutputAssembly '%s'" % (source, exe)],
               check=True, capture_output=True)

# How the update helper runs it: quietly, everything captured to a log.
RUNNER = os.path.join(scratch, "runner.ps1")
with open(RUNNER, "w", encoding="utf-8") as handle:
    handle.write("param([string]$Installer, [string]$Destination, [string]$Log)\n"
                 "& $Installer -Destination $Destination -Quiet -NoShortcuts -NoAssociation *> $Log\n"
                 "exit $LASTEXITCODE\n")


def release(label, verdict=None):
    """A setup folder like the zip's, whose program says which release it is."""
    setup = os.path.join(scratch, "setup-" + label)
    if os.path.isdir(setup):
        shutil.rmtree(setup)
    os.makedirs(os.path.join(setup, "PlatenPDF"))
    shutil.copy(INSTALLER, setup)
    shutil.copy(exe, os.path.join(setup, "PlatenPDF", "PlatenPDF.exe"))
    with open(os.path.join(setup, "PlatenPDF", "release.txt"), "w") as handle:
        handle.write(label)
    if verdict:
        with open(os.path.join(setup, "PlatenPDF", "verdict.txt"), "w") as handle:
            handle.write(verdict)
    return setup


def install(setup, destination):
    log = os.path.join(scratch, "install.log")
    run = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          RUNNER, os.path.join(setup, "install.ps1"), destination, log],
                         capture_output=True, timeout=300)
    if run.returncode and os.path.isfile(log):
        with open(log, encoding="utf-16", errors="replace") as handle:   # what *> writes
            print("     installer said:", " | ".join(
                line.strip() for line in handle.read().splitlines() if line.strip())[-700:])
    return run.returncode


def installed(destination):
    path = os.path.join(destination, "release.txt")
    return open(path).read() if os.path.isfile(path) else None


def parses(script):
    run = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "$e = $null; $t = $null; [void][System.Management.Automation.Language."
                          "Parser]::ParseFile($env:PARSE_THIS, [ref]$t, [ref]$e); $e.Count"],
                         capture_output=True, text=True, env={**os.environ, "PARSE_THIS": script})
    return run.stdout.strip() == "0"


try:
    home = os.path.join(scratch, "O'Brien Zoë")
    destination = os.path.join(home, "Platen PDF")

    print("== installing and updating ==")
    check("a first install works, though the program writes to its error stream",
          (install(release("one"), destination), installed(destination)), (0, "one"))
    check("an update replaces it", (install(release("two"), destination), installed(destination)),
          (0, "two"))
    check("and leaves nothing beside it",
          sorted(os.listdir(home)), ["Platen PDF"])
    check("its uninstaller survives an apostrophe in the path",
          parses(os.path.join(destination, "uninstall.ps1")), True)

    print("== a new copy that fails its check ==")
    check("is refused", install(release("bad", verdict="SELFTEST: FAIL"), destination), 1)
    check("and the copy that was there still is", installed(destination), "two")
    check("with no half-made copy left", sorted(os.listdir(home)), ["Platen PDF"])

    print("== the folder spelled other ways ==")
    check("a trailing backslash installs into the folder itself",
          (install(release("three"), destination + "\\"), installed(destination),
           sorted(os.listdir(home))), (0, "three", ["Platen PDF"]))
    short = subprocess.run('for %I in ("' + destination + '") do @echo %~sI', shell=True,
                           capture_output=True, text=True).stdout.strip()
    if short and short.upper() != destination.upper():
        check("its short 8.3 name installs into the real folder",
              (install(release("four"), short), installed(destination),
               sorted(os.listdir(home))), (0, "four", ["Platen PDF"]))
    else:
        print("  --   short names are off on this drive; not checked")

    print("== a swap cut short ==")
    os.rename(destination, destination + ".old")       # as a power cut would leave it
    check("the next install finds the set-aside copy and carries on",
          (install(release("five"), destination), installed(destination),
           sorted(os.listdir(home))), (0, "five", ["Platen PDF"]))
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print("\nRESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
