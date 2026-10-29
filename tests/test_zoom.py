"""The page must re-fit when the window changes size, which is what maximising does."""
import os, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []


def drive(window):
    threading.Thread(target=lambda: (time.sleep(120), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-44s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    # The panel state is stored by the backend and really does persist, so a
    # previous session could leave it collapsed and quietly change every
    # width measured below. Start from the default.
    js("window.pywebview.api.set_pref('inspectorCollapsed', false).then(restoreInspector)")
    js("window.openOnStart(%r)" % os.path.join(OUT, "fixture.pdf").replace("\\", "/"))
    time.sleep(3.5)

    check("fit page is the default", js("S.zoomMode"), "page")

    def whole_page():
        """The page fits the viewer both ways, and touches it on one side --
        fitted, not merely small. The stage pads 22px each side."""
        return js("(function(){"
                  "const v=document.getElementById('viewer');"
                  "const i=document.getElementById('pageimg');"
                  "const w=v.clientWidth-44-i.width, h=v.clientHeight-44-i.height;"
                  "return w >= -2 && h >= -2 && Math.min(w, h) <= 20;})()")

    def shortfall():
        """Unused width beside the page, in px. The stage pads 22px each side."""
        return js("(function(){"
                  "const v=document.getElementById('viewer');"
                  "const i=document.getElementById('pageimg');"
                  "return v.clientWidth - 44 - i.width;})()")

    check("whole page visible at first size", whole_page(), True)
    first_zoom = js("S.zoom")

    # The complaint this guards: maximising used to blow the page up to the
    # full width of the screen, with most of it below the fold.
    window.maximize()
    time.sleep(2.0)
    check("whole page visible when maximised", whole_page(), True)
    check("and it grew to use the room", js("S.zoom"), lambda z: z > first_zoom + 0.05)
    window.restore()
    time.sleep(2.0)
    check("whole page visible after restoring", whole_page(), True)

    js("setZoom('width')")
    time.sleep(1.5)
    small_zoom = js("S.zoom")
    check("fit width fills the window", shortfall(),
          lambda px: px is not None and -2 <= px <= 20)

    window.resize(1500, 950)
    time.sleep(2.0)
    wide_zoom = js("S.zoom")
    check("zoom grew when the window grew", wide_zoom, lambda z: z > small_zoom + 0.05)
    check("page still fills the wider window", shortfall(),
          lambda px: px is not None and -2 <= px <= 20)

    window.resize(900, 700)
    time.sleep(2.0)
    narrow_zoom = js("S.zoom")
    check("zoom shrank when the window shrank", narrow_zoom,
          lambda z: z < wide_zoom - 0.05)
    check("page still fills the narrow window", shortfall(),
          lambda px: px is not None and -2 <= px <= 20)

    js("setZoom('page')")
    time.sleep(1.5)
    check("fit page keeps the whole page visible",
          js("(function(){const v=document.getElementById('viewer');"
             "const i=document.getElementById('pageimg');"
             "return i.height <= v.clientHeight + 2;})()"), True)

    js("setZoom('fixed', 1.0)")
    time.sleep(1.5)
    fixed_zoom = js("S.zoom")
    window.resize(1400, 900)
    time.sleep(2.0)
    check("a chosen zoom is not overridden by resizing", js("S.zoom"), fixed_zoom)
    check("label reflects the chosen zoom",
          js("document.getElementById('zoomlabel').textContent"), "100%")

    js("setZoom('width')")
    time.sleep(1.5)
    check("switching back to fit width refits", shortfall(),
          lambda px: px is not None and -2 <= px <= 20)
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF zoom", url=UI, js_api=api, width=1050, height=800)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
