"""A white page must reach the screen white.

Chromium colour-manages its content to the display's ICC profile, which on a
warm or wide-gamut monitor converts white into something that is not white.
The page was rendered correctly and arrived on screen cream. Everything here
is measured on screen, because every earlier layer reported itself correct:
the renderer, the data URI and the DOM all said 255,255,255 while the window
showed 255,252,222.
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

import webview
from PIL import ImageGrab

from backend import titlebar          # imports backend, which pins sRGB
from backend.api import Api

u = ctypes.windll.user32
UI = os.path.join(ROOT, "ui", "index.html")
PDF = os.path.join(ROOT, "tests", "output", "fixture.pdf")
OUT = os.path.join(ROOT, "tests", "output", "colour.png")
PANEL = (0x24, 0x2a, 0x35)      # --panel in ui/styles.css
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-44s %s" % ("ok  " if ok else "BUG ", label, got), flush=True)


def window_at(x, y):
    return u.GetAncestor(u.WindowFromPoint(wintypes.POINT(x, y)), 2)


def rect(h):
    box = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(h), ctypes.byref(box))
    return box.left, box.top, box.right, box.bottom


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    print("== the page is white before it reaches the screen ==")
    js("window.openOnStart(%s)" % repr(PDF.replace("\\", "/")))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(3)
    check("no CSS filter on the page image",
          js("getComputedStyle(document.getElementById('pageimg')).filter"), "none")
    # Read the decoded image back out of the DOM, so a failure below can only
    # be the display layer and not the renderer.
    dom = js("""
      (function () {
        var i = document.getElementById('pageimg');
        var c = document.createElement('canvas');
        c.width = 8; c.height = 8;
        var x = c.getContext('2d');
        x.drawImage(i, 5, 5, 8, 8, 0, 0, 8, 8);
        var d = x.getImageData(2, 2, 1, 1).data;
        return [d[0], d[1], d[2]].join(',');
      })()""")
    check("the decoded image is white in the DOM", dom, "255,255,255")

    print("")
    print("== and it is still white on screen ==")
    hwnd = titlebar._our_windows()[0]
    for _ in range(16):
        u.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), 60, 60, 1200, 880,
                       0x0010 | 0x0040)
        time.sleep(0.6)
        left, top, right, bottom = rect(hwnd)
        spots = [((left + right) // 2, top + 12), (left + 30, top + 12),
                 (right - 150, top + 12), (left + 30, bottom - 30),
                 (right - 30, bottom - 30), ((left + right) // 2, (top + bottom) // 2)]
        if all(window_at(*s) == hwnd for s in spots):
            break

    left, top, right, bottom = rect(hwnd)
    spots = [((left + right) // 2, top + 12), (left + 30, top + 12),
             (right - 150, top + 12), (left + 30, bottom - 30),
             (right - 30, bottom - 30), ((left + right) // 2, (top + bottom) // 2)]
    if not all(window_at(*s) == hwnd for s in spots):
        # Never photograph what we cannot prove is ours.
        print("  SKIP something else is in front; nothing captured", flush=True)
    else:
        shot = ImageGrab.grab((left, top, right, bottom))
        shot.save(OUT)
        w, h = shot.size
        page = [shot.getpixel((int(w * fx), int(h * fy)))[:3]
                for fx, fy in ((0.45, 0.30), (0.50, 0.45), (0.55, 0.60))]
        print("  page pixels:", ["#%02x%02x%02x" % p for p in page])
        check("the page is white, not cream", page, lambda ps: all(p == (255, 255, 255) for p in ps))
        menu = shot.getpixel((w // 2, 46))[:3]
        print("  menu bar   : #%02x%02x%02x  (styles.css says #242a35)" % menu)
        check("the app's own grey matches the stylesheet", menu,
              lambda p: all(abs(a - b) <= 1 for a, b in zip(p, PANEL)))

    u.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-2), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF", url=UI, js_api=api, width=1200, height=880)
api.attach_window(w)
titlebar.attach(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
