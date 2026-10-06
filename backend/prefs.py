"""Small per-user preferences that have to outlive a restart.

The front end cannot keep these itself. pywebview serves the interface from a
loopback port that is chosen fresh on every launch, so the page's origin
changes each time and localStorage starts empty - a setting written there is
forgotten the moment the program closes, silently and with no error to notice.
Anything that should be remembered belongs on this side.
"""
from __future__ import annotations

import json
import os

from .signatures import APP_DIR

FILE = os.path.join(APP_DIR, "settings.json")

# Only keys listed here are accepted, so the front end cannot grow the file
# into a junk drawer, and a typo fails loudly rather than vanishing.
KNOWN = {
    "inspectorCollapsed": bool,
    "updateChecks": bool,
}


def all() -> dict:
    try:
        with open(FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in KNOWN} if isinstance(data, dict) else {}


def get(key: str, default=None):
    return all().get(key, default)


def set(key: str, value) -> dict:      # noqa: A001 - reads better than set_pref
    if key not in KNOWN:
        raise KeyError("Unknown setting: %s" % key)
    wanted = KNOWN[key]
    if wanted is bool:
        value = bool(value)
    current = all()
    current[key] = value
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(FILE, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=1)
    except OSError:
        # A preference we cannot write is a preference that does not stick.
        # Not worth interrupting anyone over, and the value still applies for
        # this session because the caller already acted on it.
        pass
    return current
