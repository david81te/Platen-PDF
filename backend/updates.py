"""Telling someone a newer Platen PDF exists.

This is the one place the program reaches the internet without being asked,
so it is deliberately modest: one request to the public releases list, at most
once a day, no identifiers of any kind, and a switch to turn it off. Nothing
is sent - it is a read of a public page, the same one a browser would fetch.

It never installs anything by itself. It says a new version exists and offers
the download; replacing a running program underneath someone is a good way to
lose their work.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import version
from .signatures import APP_DIR

RELEASES_API = "https://api.github.com/repos/david81te/Platen-PDF/releases/latest"
RELEASES_PAGE = "https://github.com/david81te/Platen-PDF/releases/latest"
STATE_FILE = os.path.join(APP_DIR, "updates.json")
INTERVAL = 24 * 60 * 60


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
    state.update({
        "checked": time.time(),
        "latest": latest,
        "notes": (release.get("body") or "")[:400],
    })
    _save(state)
    answer.update({
        "latest": latest,
        "notes": state["notes"],
        "checked": state["checked"],
        "update_available": bool(latest and is_newer(latest, current)),
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
