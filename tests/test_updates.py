"""Update notices: right answer, politely, and never in the way.

The version comparison gets the most attention here because it is the part
that fails silently. A string comparison says 1.10 is older than 1.9, so
everybody stops being told about updates and nobody notices for months.
"""
import io
import os
import sys
import time
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402  - keeps the state file out of the real folder

from backend import updates, version  # noqa: E402

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


print("== comparing versions ==")
for newer, older, expected in [
    ("1.0.1", "1.0.0", True),
    ("1.1.0", "1.0.9", True),
    ("2.0.0", "1.9.9", True),
    ("1.10.0", "1.9.0", True),      # the one a string comparison gets wrong
    ("1.0.10", "1.0.9", True),      # and this one
    ("v1.1.0", "1.0.0", True),      # tags carry a leading v
    ("1.0.0", "1.0.0", False),
    ("1.0.0", "1.0.1", False),
    ("1.9.0", "1.10.0", False),
    ("1.0", "1.0.0", False),        # shorter is not newer
    ("1.0.0.1", "1.0.0", True),
]:
    check("%-8s newer than %-8s" % (newer, older),
          updates.is_newer(newer, older), expected)

check("rubbish does not claim to be newer", updates.is_newer("", version.VERSION), False)
check("nor does a garbage tag", updates.is_newer("banana", version.VERSION), False)

print("")
print("== the switch ==")
check("checking is on by default", updates.enabled(), True)
updates.set_enabled(False)
check("it can be turned off", updates.enabled(), False)
quiet = updates.check()
check("and then it says nothing", quiet["update_available"], False)
check("while still reporting the current version", quiet["current"], version.VERSION)
updates.set_enabled(True)
check("and back on again", updates.enabled(), True)

print("")
print("== a real look at the releases page ==")
import ssl  # noqa: E402
import truststore  # noqa: E402
# Without this a PC that has not yet met GitHub's root certificate fails
# every check, and fails quietly - see backend/__init__.py.
check("HTTPS is verified by Windows, not a fixed list",
      ssl.SSLContext is truststore.SSLContext, True)
found = updates.check(force=True)
check("it reached GitHub and read a tag", bool(found["latest"]), lambda v: bool(v))
check("the current version is ours", found["current"], version.VERSION)
# Deliberately not a hardcoded version number. The point is that a copy built
# from this tree never tells its own users to go and fetch a release they are
# already running. When this fails, version.py is behind the newest published
# release and wants bumping before the next build.
check("this tree is not behind the published release (%s)" % found["latest"],
      found["update_available"], False)
check("a download page is offered", "github.com" in found["url"], True)
check("GitHub answered this time", found["reached"], True)
asset = updates._state().get("asset") or {}
check("the installer download is known", asset.get("url", ""),
      lambda u: u.startswith("https://github.com/") and u.endswith(updates.SETUP_ASSET))
check("with a checksum to prove it by", asset.get("digest", ""),
      lambda d: d.startswith("sha256:"))

print("")
print("== it does not ask GitHub on every launch ==")
first = updates.check()
second = updates.check()
check("the second call is served from cache", second["checked"], first["checked"])

print("")
print("== when a newer version does appear ==")
state = updates._state()
state.update({"latest": "99.0.0", "checked": time.time(), "dismissed": None})
updates._save(state)
check("it is noticed", updates.check()["update_available"], True)
nudge = updates.should_mention()
check("and the window is told to mention it", nudge["show"], True)
check("naming the version", nudge.get("latest"), "99.0.0")

updates.dismiss("99.0.0")
check("dismissing it stops the nudge", updates.should_mention()["show"], False)
state = updates._state()
state["latest"] = "99.1.0"
updates._save(state)
check("but a later version speaks up again", updates.should_mention()["show"], True)

print("")
print("== offline is quiet, not broken ==")
real = updates.RELEASES_API
updates.RELEASES_API = "https://this-host-does-not-exist-%s.invalid/x" % uuid.uuid4().hex[:6]
state = updates._state()
state["checked"] = 0
updates._save(state)
try:
    offline = updates.check(force=True)
    check("no exception escapes", True, True)
    check("and the known version is kept", offline["latest"], "99.1.0")
except Exception as exc:                                  # noqa: BLE001
    check("no exception escapes", "raised %s" % type(exc).__name__, True)
finally:
    updates.RELEASES_API = real

print("")
print("== installing an update ==")
# A fake release served from this machine. The installer inside it only
# records how it was called; the self-test and version stamp are stood in for,
# since the real ones belong to a 140 MB build.
import hashlib  # noqa: E402
import http.server  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import zipfile  # noqa: E402

FAKE_INSTALLER = r"""param([string]$Destination, [switch]$NoShortcuts, [switch]$Quiet)
New-Item -ItemType Directory -Force $Destination | Out-Null
Set-Content -LiteralPath (Join-Path $Destination 'PlatenPDF.exe') -Value 'stub'
@{ destination = $Destination; quiet = [bool]$Quiet; noshortcuts = [bool]$NoShortcuts;
   at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'marker.json')
exit 0
"""

scratch = tempfile.mkdtemp(prefix="platen-update-")
served = os.path.join(scratch, "served")
os.makedirs(served)


def make_zip(name, entries):
    path = os.path.join(served, name)
    with zipfile.ZipFile(path, "w") as bundle:
        for entry, data in entries.items():
            bundle.writestr(entry, data)
    with open(path, "rb") as handle:
        data = handle.read()
    return {"size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()}


GOOD = {"install.ps1": FAKE_INSTALLER, "Install Platen PDF.cmd": "@echo off",
        "PlatenPDF/PlatenPDF.exe": b"MZ not really"}
good = make_zip("good.zip", GOOD)
escape = make_zip("escape.zip", {**GOOD, "../../outside.txt": "gotcha"})
hollow = make_zip("hollow.zip", {"readme.txt": "no installer here"})


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=served, **kwargs)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d/" % server.server_address[1]

saved = (updates.WORK_DIR, updates._allowed, updates.selftest, updates.file_version)
updates.WORK_DIR = os.path.join(scratch, "work")
updates._allowed = lambda url: url.startswith(base)
updates.selftest = lambda exe: True
updates.file_version = lambda path: "99.0.0"


def attempt(asset_name, meta, **change):
    """Offer `asset_name` as release 99.0.0 and run the download to its end."""
    asset = {"url": base + asset_name, **meta, **change}
    state = updates._state()
    state.update({"latest": "99.0.0", "asset": asset, "checked": time.time()})
    updates._save(state)
    updates._set(stage="idle")
    updates.start_download()
    deadline = time.time() + 60
    while time.time() < deadline:
        job = updates.progress()
        if job["stage"] in ("ready", "failed"):
            return job
        time.sleep(0.1)
    return {"stage": "timed out"}


try:
    job = attempt("good.zip", good)
    check("a good release downloads and checks out", job["stage"], "ready")
    check("unpacked with its installer beside it",
          os.path.isfile(os.path.join(job.get("setup", ""), "install.ps1")), True)
    check("progress counted every byte", job.get("done"), good["size"])

    job = attempt("good.zip", good, digest="sha256:" + "0" * 64)
    check("a download that differs from the release is refused",
          job.get("error", ""), lambda e: "did not match" in e)
    check("and is not offered for install", job["stage"], "failed")

    job = attempt("good.zip", good, size=good["size"] + 1)
    check("a short download is refused", job.get("error", ""), lambda e: "incomplete" in e)

    job = attempt("escape.zip", escape)
    check("a zip that writes outside its folder is refused",
          job.get("error", ""), lambda e: "outside its folder" in e)
    check("and wrote nothing outside it",
          os.path.exists(os.path.join(scratch, "outside.txt")), False)

    job = attempt("hollow.zip", hollow)
    check("a zip with no installer is refused",
          job.get("error", ""), lambda e: "not a Platen PDF installer" in e)

    updates.selftest = lambda exe: False
    job = attempt("good.zip", good)
    check("a build that fails its self-test is refused",
          job.get("error", ""), lambda e: "did not pass its own check" in e)
    updates.selftest = lambda exe: True

    updates.file_version = lambda path: "98.0.0"
    job = attempt("good.zip", good)
    check("a build of the wrong version is refused",
          job.get("error", ""), lambda e: "is version 98.0.0" in e)
    updates.file_version = lambda path: "99.0.0"

    job = attempt("missing.zip", good)
    check("a download that fails says so", job.get("error", ""),
          lambda e: "did not finish" in e)

    updates._allowed = saved[1]
    job = attempt("good.zip", good)
    check("only GitHub is downloaded from", job.get("error", ""),
          lambda e: "somewhere unexpected" in e)
    updates._allowed = lambda url: url.startswith(base)

    state = updates._state()
    state["latest"] = version.VERSION
    updates._save(state)
    try:
        updates.start_download()
        check("nothing to install when already current", "started anyway", False)
    except ValueError:
        check("nothing to install when already current", True, True)

    check("a copy run from source cannot install itself", updates.install_target(), None)
    try:
        updates.launch_installer([])
        check("so it refuses to try", "tried", False)
    except ValueError:
        check("so it refuses to try", True, True)

    # The hand-over: the helper must wait for the old copy to exit, then run
    # the release's installer quietly into the install folder.
    job = attempt("good.zip", good)
    destination = os.path.join(scratch, "installed")
    leaving = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    helper = updates.launch_installer([__file__], destination=destination,
                                      wait_for=leaving.pid, launch=False)
    leaving.wait()
    gone = time.time()
    code = helper.wait(timeout=120)
    check("the helper finished cleanly", code, 0)
    marker = os.path.join(destination, "marker.json")
    record = {}
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8-sig") as handle:
            record = json.load(handle)
    check("it ran the installer into the install folder",
          os.path.normcase(record.get("destination", "")), os.path.normcase(destination))
    check("quietly, leaving shortcuts alone",
          (record.get("quiet"), record.get("noshortcuts")), (True, True))
    check("and only once the old copy had closed",
          record.get("at", 0) / 1000.0 >= gone - 0.5, True)
    with open(os.path.join(updates.WORK_DIR, "plan.json"), encoding="utf-8") as handle:
        plan = json.load(handle)
    check("the open documents are passed on to reopen", plan["reopen"], [__file__])
finally:
    server.shutdown()
    updates.WORK_DIR, updates._allowed, updates.selftest, updates.file_version = saved
    updates._set(stage="idle")

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
