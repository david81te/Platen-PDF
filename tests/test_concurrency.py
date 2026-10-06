"""Two calls must never reach the same document at once.

pywebview runs every call from JavaScript on its own thread, so a save can
overlap a render, a page rotation, an undo. The document is one mutable object
and the undo stack assumes a single writer, so when one call swaps it out
mid-flight PyMuPDF answers the other with "document closed".

That surfaced for months as an intermittent "menu: save" failure in
test_buttons, passing often enough to be written off as a flake. The cause was
that save was the only mutating endpoint not taking the lock.

This hammers the surface from several threads at once. It is deliberately
unkind: the point is to lose the race on purpose, repeatedly, rather than wait
for a user to find it.
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402

import pymupdf as fitz  # noqa: E402

from backend.api import Api  # noqa: E402

OUT = os.path.join(ROOT, "tests", "output")
SOURCE = os.path.join(OUT, "fixture.pdf")
WORKING = os.path.join(OUT, "concurrency.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def fresh_copy():
    doc = fitz.open(SOURCE)
    doc.save(WORKING)
    doc.close()


print("== the lock covers every mutating endpoint ==")
# Catch a new endpoint added without the decorator, rather than waiting for it
# to lose a race in front of someone.
import inspect  # noqa: E402

# attach_window assigns one field and never looks at a document, so it has
# nothing to race over. Everything else on the public surface does.
TOUCHES_NO_DOCUMENT = {"attach_window"}

unguarded = []
for name, member in inspect.getmembers(Api, predicate=inspect.isfunction):
    if name.startswith("_") or name in TOUCHES_NO_DOCUMENT:
        continue
    source = inspect.getsource(member)
    guarded = getattr(member, "__wrapped__", None) is not None or "with _LOCK" in source
    if not guarded:
        unguarded.append(name)
check("nothing that touches a document runs unguarded", unguarded, [])

print("")
print("== save against everything else, at the same time ==")
fresh_copy()
api = Api()
api.open_path(WORKING)
errors = []
rounds = {"saves": 0, "others": 0}
stop = threading.Event()


def saver():
    while not stop.is_set():
        result = api.save()
        rounds["saves"] += 1
        if not result.get("ok"):
            errors.append(("save", result.get("error")))
        time.sleep(0.004)


def masher(fn, label):
    def run():
        while not stop.is_set():
            try:
                result = fn()
                rounds["others"] += 1
                if isinstance(result, dict) and not result.get("ok", True):
                    message = str(result.get("error", ""))
                    # "Open a document first" is a fair answer mid-close; a
                    # document closed underneath a live call is not.
                    if "closed" in message.lower():
                        errors.append((label, message))
            except Exception as exc:                      # noqa: BLE001
                errors.append((label, repr(exc)))
            time.sleep(0.003)
    return run


threads = [threading.Thread(target=saver, daemon=True)]
for fn, label in (
    (lambda: api.render(0, 1.0), "render"),
    (lambda: api.page_sizes(), "page_sizes"),
    (lambda: api.page_rotate([0], 90), "page_rotate"),
    (lambda: api.undo(), "undo"),
    (lambda: api.redo(), "redo"),
    (lambda: api.thumbs(0, 1, 90), "thumbs"),
    (lambda: api.doc_info(), "doc_info"),
    (lambda: api.unsaved(), "unsaved"),
):
    threads.append(threading.Thread(target=masher(fn, label), daemon=True))

for thread in threads:
    thread.start()
time.sleep(9)
stop.set()
for thread in threads:
    thread.join(timeout=5)

print("  saves attempted : %d" % rounds["saves"])
print("  other calls     : %d" % rounds["others"])
check("the save thread ran often enough to mean something",
      rounds["saves"], lambda n: n > 25)
check("the other threads did too", rounds["others"], lambda n: n > 100)
check("nothing reported a closed document", errors[:6], [])

print("")
print("== and the file is still readable afterwards ==")
try:
    doc = fitz.open(WORKING)
    pages = doc.page_count
    text = doc[0].get_text()
    doc.close()
except Exception as exc:                                   # noqa: BLE001
    pages, text = 0, ""
    fails.append(("the saved file could not be reopened", repr(exc)))
check("it still opens", pages, lambda n: n >= 1)
check("and still has its text", bool(text.strip()), True)

try:
    os.remove(WORKING)
except OSError:
    pass

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
