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
from collections import Counter
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


def client_origin(h):
    """Where the web content starts, in screen coordinates.

    The window rect includes the caption bar, the page does not, so probing by
    a fraction of the window is a guess. This turns a position inside the page
    element into the exact pixel to look at.
    """
    point = wintypes.POINT(0, 0)
    u.ClientToScreen(wintypes.HWND(h), ctypes.byref(point))
    return point.x, point.y


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

        # Look at the page itself rather than at a fraction of the window.
        # Fractions were a guess, and a short page or a scrolled view put one
        # of them on the grey behind the page - which reads as "not white"
        # while telling us nothing about the colour of the page.
        # The part of the page actually on screen. A page is routinely taller
        # than the window, so a fraction of the whole page element can sit far
        # below the bottom edge; only the visible overlap can be photographed.
        box = js("""(function () {
            var r = document.getElementById('pageimg').getBoundingClientRect();
            var l = Math.max(r.left, 0), t = Math.max(r.top, 0);
            var rt = Math.min(r.right, window.innerWidth);
            var bt = Math.min(r.bottom, window.innerHeight);
            return [l, t, rt - l, bt - t, window.devicePixelRatio || 1];
          })()""")
        check("a usable amount of the page is on screen",
              [round(box[2]), round(box[3])],
              lambda d: d[0] >= 80 and d[1] >= 80)
        cx, cy = client_origin(hwnd)
        scale = box[4]
        # Sample a grid over the page rather than a few chosen spots. Any
        # single point can land on a letter - the fixture has text on it - and
        # an antialiased glyph edge is grey, which says nothing about the
        # colour of the paper. What does say it is the commonest colour on the
        # page: on a white page that is pure white, and on the cream page this
        # test exists to catch, every one of those samples shifts together.
        spots_on_page = []
        for row in range(11):
            for column in range(11):
                fx = 0.04 + 0.92 * column / 10.0
                fy = 0.04 + 0.92 * row / 10.0
                spots_on_page.append(
                    (cx + (box[0] + box[2] * fx) * scale - left,
                     cy + (box[1] + box[3] * fy) * scale - top))
        inside = all(0 <= x < w and 0 <= y < h for x, y in spots_on_page)
        check("the probe points land inside the captured window", inside, True)
        page = [shot.getpixel((int(x), int(y)))[:3] for x, y in spots_on_page
                if 0 <= x < w and 0 <= y < h]
        tally = Counter(page)
        common, seen = tally.most_common(1)[0]
        print("  sampled %d points on the page; commonest #%02x%02x%02x x%d"
              % (len(page), common[0], common[1], common[2], seen))
        check("the paper itself is white, not cream", common, (255, 255, 255))
        check("and most of the page is that colour, so it is the paper "
              "and not a mark on it", seen, lambda n: n > len(page) * 0.5)
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
