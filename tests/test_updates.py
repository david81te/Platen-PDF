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
found = updates.check(force=True)
check("it reached GitHub and read a tag", bool(found["latest"]), lambda v: bool(v))
check("the current version is ours", found["current"], version.VERSION)
check("1.0.0 is published, so there is nothing newer yet",
      found["update_available"], False)
check("a download page is offered", "github.com" in found["url"], True)

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
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
