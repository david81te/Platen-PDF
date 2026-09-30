"""The window caption must be dark even when Windows is set to Light mode.

pywebview ties the caption to the system theme, so on a light-themed PC it
draws a white bar above the dark app. backend/titlebar.py overrides that.
This checks the real pixels on screen rather than trusting the API call.
"""
import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from PIL import ImageGrab

from backend import titlebar
from backend.api import Api

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "ui", "index.html")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
fails = []

HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
NOACTIVATE_SHOW = 0x0010 | 0x0040
NOMOVE_NOSIZE_NOACTIVATE = 0x0001 | 0x0002 | 0x0010


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-46s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def window_at(x, y):
    """The top-level window owning the pixel at (x, y)."""
    under = ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y))
    return ctypes.windll.user32.GetAncestor(under, 2)   # GA_ROOT


def rect(hwnd):
    box = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(box))
    return box.left, box.top, box.right, box.bottom


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    time.sleep(3)

    print("== the module finds our window and the calls succeed ==")
    handles = titlebar._our_windows()
    check("a top-level window was found", len(handles), lambda n: n >= 1)
    check("paint() reports success", titlebar.paint(), True)
    check("pywebview's own check now says dark",
          titlebar.force_dark_detection(), True)

    print("")
    print("== and the caption is actually dark on screen ==")
    hwnd = handles[0]
    # Park it somewhere predictable and raise it. NOACTIVATE means focus stays
    # wherever the user left it, so this never interrupts typing elsewhere.
    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST),
                                      60, 60, 1100, 800, NOACTIVATE_SHOW)

    # Never photograph the screen without first proving our window is the thing
    # in front of the camera. A grab is blind to what it captures: if anything
    # else covers these coordinates it lands on disk instead, and whatever the
    # user happens to have open is none of this test's business.
    left, top, right, bottom = rect(hwnd)
    probe = ((left + right) // 2, top + 12)
    for _ in range(20):
        if window_at(*probe) == hwnd:
            break
        # Re-assert it: the WebView2 host finishes initialising after we first
        # ask, and puts itself back in the z-order when it does.
        ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd),
                                          wintypes.HWND(HWND_TOPMOST),
                                          60, 60, 1100, 800, NOACTIVATE_SHOW)
        time.sleep(0.5)

    if window_at(*probe) != hwnd:
        # Environmental, not a regression: another topmost window is in the
        # way. Say so and check nothing rather than photograph it.
        print("  SKIP something else is in front; nothing captured", flush=True)
    else:
        shot = ImageGrab.grab((left, top, right, bottom))
        shot.save(os.path.join(OUT, "titlebar.png"))
        width = right - left
        # Well inside the caption: past the icon and title, short of the buttons.
        samples = [shot.getpixel((x, 12))[:3]
                   for x in (int(width * 0.35), int(width * 0.5), int(width * 0.62))]
        print("  caption pixels:", ["#%02x%02x%02x" % p for p in samples])
        check("caption is dark, not white",
              max(sum(p) / 3 for p in samples), lambda v: v < 90)
        check("caption matches the app's --panel (#242a35)", samples[0],
              lambda p: all(abs(a - b) <= 12 for a, b in zip(p, (0x24, 0x2a, 0x35))))
        menu = shot.getpixel((int(width * 0.5), 45))[:3]
        print("  menu bar pixel: #%02x%02x%02x" % menu)
        check("no white seam between caption and menu bar",
              abs(sum(menu) / 3 - sum(samples[0]) / 3), lambda d: d < 20)

    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_NOTOPMOST),
                                      0, 0, 0, 0, NOMOVE_NOSIZE_NOACTIVATE)
    window.destroy()


# The real Api goes in, otherwise the page fills with "unknown action" toasts
# and the screenshot no longer shows what the window really looks like.
api = Api()
w = webview.create_window("PDF Studio", url=UI, js_api=api, width=1100, height=800)
api.attach_window(w)
titlebar.attach(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
