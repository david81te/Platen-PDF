"""Telling someone a newer Platen PDF exists.

This is the one place the program reaches the internet without being asked,
so it is deliberately modest: one request to the public releases list, at most
once a day, no identifiers of any kind, and a switch to turn it off. Nothing
is sent - it is a read of a public page, the same one a browser would fetch.

It never installs anything by itself. It says a new version exists, and only
when someone presses Install does it download the release, prove it (checksum,
version, the new program's own self-test) and only then close Platen PDF, run
the release's installer and reopen what was open. Everything that can fail
happens before anything is replaced; replacing a running program underneath
someone is a good way to lose their work.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import version
from .signatures import APP_DIR

RELEASES_API = "https://api.github.com/repos/david81te/Platen-PDF/releases/latest"
RELEASES_PAGE = "https://github.com/david81te/Platen-PDF/releases/latest"
STATE_FILE = os.path.join(APP_DIR, "updates.json")
INTERVAL = 24 * 60 * 60

SETUP_ASSET = "PlatenPDF-Setup.zip"
# Outside the install folder, which the installer deletes and rebuilds.
WORK_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                        "PlatenPDF-update")
# Where a release download may start. GitHub then redirects to its file host.
DOWNLOAD_HOSTS = {"github.com"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1)
    except OSError:
        pass            # a check we cannot remember is not worth an error


def _numbers(text: str) -> tuple:
    """'v1.10.2' -> (1, 10, 2). Compares as numbers, so 1.10 beats 1.9."""
    cleaned = (text or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    parts = []
    for chunk in cleaned.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    left, right = _numbers(candidate), _numbers(current)
    size = max(len(left), len(right))
    left += (0,) * (size - len(left))
    right += (0,) * (size - len(right))
    return left > right


def enabled() -> bool:
    return _state().get("enabled", True)


def set_enabled(on: bool) -> dict:
    state = _state()
    state["enabled"] = bool(on)
    _save(state)
    return {"enabled": state["enabled"]}


def check(force: bool = False) -> dict:
    """Look for a newer release. Never raises; offline simply means no news."""
    state = _state()
    current = version.VERSION
    answer = {
        "current": current,
        "latest": state.get("latest"),
        "update_available": False,
        "url": RELEASES_PAGE,
        "notes": state.get("notes"),
        "checked": state.get("checked"),
        "enabled": state.get("enabled", True),
        "installable": install_target() is not None,
        # Whether GitHub answered this time, so "you are up to date" is never
        # said on the strength of an old answer while offline.
        "reached": False,
    }
    if not answer["enabled"] and not force:
        return answer

    fresh = time.time() - state.get("checked", 0) < INTERVAL
    if fresh and not force:
        answer["update_available"] = bool(
            state.get("latest") and is_newer(state["latest"], current))
        return answer

    try:
        request = urllib.request.Request(
            RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "PlatenPDF/%s" % current})
        with urllib.request.urlopen(request, timeout=12) as response:
            release = json.loads(response.read())
    except (urllib.error.URLError, ValueError, OSError):
        # No internet, GitHub down, rate limited: all the same to a user who
        # did not ask for this. Keep whatever was known before.
        state["checked"] = time.time()
        _save(state)
        answer["checked"] = state["checked"]
        return answer

    latest = release.get("tag_name") or ""
    asset = next((a for a in release.get("assets") or []
                  if a.get("name") == SETUP_ASSET), None)
    state.update({
        "checked": time.time(),
        "latest": latest,
        "notes": (release.get("body") or "")[:4000],
        "asset": {"url": asset.get("browser_download_url"),
                  "size": asset.get("size") or 0,
                  "digest": asset.get("digest") or ""} if asset else None,
    })
    _save(state)
    answer.update({
        "latest": latest,
        "notes": state["notes"],
        "checked": state["checked"],
        "update_available": bool(latest and is_newer(latest, current)),
        "reached": True,
    })
    return answer


def dismiss(tag: str) -> dict:
    """Stop mentioning this particular version; a later one still speaks up."""
    state = _state()
    state["dismissed"] = tag
    _save(state)
    return {"dismissed": tag}


def should_mention() -> dict:
    """What the window should show on startup, if anything."""
    found = check()
    if not found["update_available"]:
        return {"show": False}
    if _state().get("dismissed") == found["latest"]:
        return {"show": False}
    return {"show": True, **found}


# ---- installing --------------------------------------------------------------

def install_target() -> str | None:
    """The folder this copy was installed into, or None if it cannot update itself.

    Only a copy the installer put in place can: the installer leaves its
    uninstaller beside the exe. The portable single file and a run from source
    are pointed at the download page instead.
    """
    if not getattr(sys, "frozen", False):
        return None
    folder = os.path.dirname(os.path.abspath(sys.executable))
    if not os.path.isfile(os.path.join(folder, "Uninstall Platen PDF.cmd")):
        return None
    return folder


class UpdateError(Exception):
    """A reason, worded for the person, that the update stopped."""


_job: dict = {"stage": "idle"}      # idle | downloading | checking | ready | failed
_job_lock = threading.Lock()


def _set(**fields) -> None:
    with _job_lock:
        _job.update(fields)


def progress() -> dict:
    with _job_lock:
        return dict(_job)


def start_download() -> dict:
    """Fetch and prove the newer release in the background; poll progress()."""
    state = _state()
    latest, asset = state.get("latest"), state.get("asset")
    if not (latest and is_newer(latest, version.VERSION)):
        raise ValueError("There is no newer version to install.")
    if not (asset and asset.get("url")):
        raise ValueError("That release has no installer to download.")
    with _job_lock:
        if _job.get("stage") in ("downloading", "checking"):
            return dict(_job)
        if _job.get("stage") == "ready" and _job.get("version") == latest:
            return dict(_job)
        _job.clear()
        _job.update(stage="downloading", version=latest, done=0,
                    total=int(asset.get("size") or 0))
    threading.Thread(target=_prepare, args=(latest, asset), daemon=True).start()
    return progress()


def _prepare(tag: str, asset: dict) -> None:
    try:
        setup = _download_and_check(tag, asset)
    except UpdateError as exc:
        _set(stage="failed", error=str(exc))
    except Exception as exc:                          # noqa: BLE001
        _set(stage="failed", error="The update could not be prepared (%s). "
                                   "Nothing was changed." % type(exc).__name__)
    else:
        _set(stage="ready", setup=setup)


def _allowed(url: str) -> bool:
    parts = urllib.parse.urlparse(url)
    return parts.scheme == "https" and parts.hostname in DOWNLOAD_HOSTS


def _download_and_check(tag: str, asset: dict) -> str:
    """Download, verify and unpack the release; return the unpacked folder.

    Nothing here touches the installed copy, so any failure leaves it as it was.
    """
    url = asset["url"]
    if not _allowed(url):
        raise UpdateError("The release points somewhere unexpected, so it was not downloaded.")

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    archive = os.path.join(WORK_DIR, SETUP_ASSET)
    digest = hashlib.sha256()
    received = 0
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "PlatenPDF/%s" % version.VERSION})
        with urllib.request.urlopen(request, timeout=30) as response, \
                open(archive, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                _set(done=received)
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError("The download did not finish. Check the internet "
                          "connection and try again.") from exc

    expected_size = int(asset.get("size") or 0)
    if expected_size and received != expected_size:
        raise UpdateError("The download was incomplete. Try again.")
    expected = (asset.get("digest") or "").lower()
    if expected.startswith("sha256:") and digest.hexdigest() != expected[7:]:
        raise UpdateError("The download did not match the published release, "
                          "so it was not installed.")

    _set(stage="checking")
    setup = os.path.join(WORK_DIR, "setup")
    _unpack(archive, setup)
    exe = os.path.join(setup, "PlatenPDF", "PlatenPDF.exe")
    if not (os.path.isfile(exe) and os.path.isfile(os.path.join(setup, "install.ps1"))):
        raise UpdateError("The download is not a Platen PDF installer.")

    found = file_version(exe)
    if found and (is_newer(found, tag) or is_newer(tag, found)):
        raise UpdateError("The download is version %s, not %s." % (found, tag.lstrip("vV")))
    if not selftest(exe):
        raise UpdateError("The new version did not pass its own check, so nothing was changed.")
    return setup


def _unpack(archive: str, target: str) -> None:
    """Extract, refusing any entry that would land outside `target`."""
    root = os.path.realpath(target)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                destination = os.path.realpath(os.path.join(root, name))
                if os.path.commonpath([root, destination]) != root:
                    raise UpdateError("The download contains a file that would be "
                                      "written outside its folder, so it was not used.")
            bundle.extractall(root)
    except zipfile.BadZipFile as exc:
        raise UpdateError("The download is damaged. Try again.") from exc


def file_version(path: str) -> str | None:
    """The version stamped into an exe's file properties, e.g. '1.6.2'."""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
    except Exception:                                  # noqa: BLE001
        return None
    high, low = info["FileVersionMS"], info["FileVersionLS"]
    return "%d.%d.%d" % (high >> 16, high & 0xFFFF, low >> 16)


def selftest(exe: str) -> bool:
    """Run the new program's own check. It is windowed, so its output is piped."""
    try:
        run = subprocess.run([exe, "--selftest"], capture_output=True, timeout=300,
                             creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    output = (run.stdout or b"") + (run.stderr or b"")
    return b"SELFTEST: PASS" in output


# Runs after Platen PDF has closed. Waits for it to be gone, because a running
# copy holds the files being replaced; gives up rather than force it, because
# a copy that will not close may be one with a question on screen.
HELPER = r"""param([string]$Plan)
$p = Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
try { Wait-Process -Id $p.wait_for -Timeout 120 -ErrorAction Stop } catch {}
if (Get-Process -Id $p.wait_for -ErrorAction SilentlyContinue) { exit 1 }
try {
    & $p.installer -Destination $p.destination -Quiet -NoShortcuts *> $p.log
    $code = $LASTEXITCODE
} catch {
    $_ | Out-File -LiteralPath $p.log -Append
    $code = 1
}
$exe = Join-Path $p.destination 'PlatenPDF.exe'
if ($code -eq 0 -and (Test-Path -LiteralPath $exe)) {
    if ($p.launch) {
        $files = @($p.reopen | ForEach-Object { '"' + $_ + '"' })
        if ($files.Count) { Start-Process -FilePath $exe -ArgumentList $files }
        else { Start-Process -FilePath $exe }
    }
    exit 0
}
if ($p.launch) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "The update could not be installed.`n`nTo install it yourself, run " +
        "Install Platen PDF in:`n" + $p.setup + "`n`nDetails: " + $p.log,
        'Platen PDF') | Out-Null
}
exit 1
"""


def launch_installer(reopen: list[str], destination: str | None = None,
                     wait_for: int | None = None, launch: bool = True):
    """Hand over to the helper, which installs once this process has exited.

    The caller closes the window straight after. `destination`, `wait_for` and
    `launch` exist for the tests, which install into a scratch folder and must
    not start programs or put up message boxes.
    """
    target = destination or install_target()
    if not target:
        raise ValueError("This copy of Platen PDF was not installed with its "
                         "installer, so it cannot update itself. Download the "
                         "new version instead.")
    job = progress()
    if job.get("stage") != "ready":
        raise ValueError("The update has not finished downloading.")
    plan = {
        "wait_for": wait_for or os.getpid(),
        "installer": os.path.join(job["setup"], "install.ps1"),
        "setup": job["setup"],
        "destination": target,
        "log": os.path.join(WORK_DIR, "install.log"),
        "reopen": [p for p in reopen if p and os.path.isfile(p)],
        "launch": launch,
    }
    plan_path = os.path.join(WORK_DIR, "plan.json")
    helper_path = os.path.join(WORK_DIR, "finish-update.ps1")
    with open(plan_path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle)
    with open(helper_path, "w", encoding="utf-8") as handle:
        handle.write(HELPER)
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", helper_path, plan_path]
    # A console nobody sees, which the installer inherits rather than opening
    # windows of its own; and a group of its own, so closing us does not stop it.
    hidden = _NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    try:
        # Out of any job object too, so closing this process cannot take it along.
        return subprocess.Popen(command, creationflags=hidden | breakaway,
                                close_fds=True)
    except OSError:
        return subprocess.Popen(command, creationflags=hidden, close_fds=True)
