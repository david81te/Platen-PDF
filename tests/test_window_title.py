"""The title bar and the taskbar name the document in front.

Reported from use: with a PDF open, the taskbar said only "Platen PDF", so
every open copy looked the same. The window was created with that title and
nothing ever renamed it. This reads the real window's caption - what the
taskbar and Alt+Tab show - rather than trusting the call that sets it.
"""
import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: E402

import pymupdf as fitz  # noqa: E402
import webview  # noqa: E402

from backend import titlebar  # noqa: E402
from backend.api import Api, window_title  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")
FIRST = os.path.join(sandbox.DIR, "Quarterly report.pdf")
SECOND = os.path.join(sandbox.DIR, "invoice-0042.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def build(path, word):
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text(fitz.Point(40, 80), word, fontsize=22)
    doc.save(path)
    doc.close()


build(FIRST, "FIRST")
build(SECOND, "SECOND")

print("== the title itself ==")
check("a document's name, then ours", window_title("a.pdf"), "a.pdf - Platen PDF")
check("no document: just ours", window_title(None), "Platen PDF")
check("an empty name counts as none", window_title("  "), "Platen PDF")
check("line breaks cannot reach the caption",
      window_title("a\nb.pdf"), "a b.pdf - Platen PDF")
check("a very long name is cut short",
      len(window_title("x" * 1000)), lambda n: n < 220)


def caption():
    """What Windows itself holds as our window's title."""
    for hwnd in titlebar._our_windows():
        length = ctypes.windll.user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
        if buffer.value:
            return buffer.value
    return None


def wait_for(want, seconds=8.0):
    deadline = time.time() + seconds
    got = caption()
    while got != want and time.time() < deadline:
        time.sleep(0.2)
        got = caption()
    return got


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.confirm = () => true;")

    print("")
    print("== on the real window ==")
    check("nothing open: the plain name", wait_for("Platen PDF"), "Platen PDF")

    js("window.openOnStart(%r)" % FIRST.replace("\\", "/"))
    check("opening a document names it",
          wait_for("Quarterly report.pdf - Platen PDF"),
          "Quarterly report.pdf - Platen PDF")

    js("window.openOnStart(%r)" % SECOND.replace("\\", "/"))
    check("opening another names that one",
          wait_for("invoice-0042.pdf - Platen PDF"), "invoice-0042.pdf - Platen PDF")

    js("showTabInPane(focusedPane, PANES[focusedPane].tabs[0])")
    check("going back to the first tab names it again",
          wait_for("Quarterly report.pdf - Platen PDF"),
          "Quarterly report.pdf - Platen PDF")

    js("dropTab(PANES[0].tabs[1], 'right')")
    time.sleep(3.5)
    check("split: the pane in front is named",
          wait_for("invoice-0042.pdf - Platen PDF"), "invoice-0042.pdf - Platen PDF")
    js("focusPane(focusedPane === 0 ? 1 : 0)")
    check("putting the other pane in front names its document",
          wait_for("Quarterly report.pdf - Platen PDF"),
          "Quarterly report.pdf - Platen PDF")
    js("closeSplit(focusedPane)")
    time.sleep(3.0)

    js("closeTab(PANES[focusedPane].tab)")
    check("closing it names the one left on screen",
          wait_for("invoice-0042.pdf - Platen PDF"), "invoice-0042.pdf - Platen PDF")
    js("closeTab(PANES[focusedPane].tab)")
    check("closing the last puts the plain name back",
          wait_for("Platen PDF"), "Platen PDF")

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF", url=UI, js_api=api, width=1100, height=800)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
