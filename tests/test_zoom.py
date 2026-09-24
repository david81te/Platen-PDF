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
        if js("!!(window.pywebview && window.pywebview.api && window.S)"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % os.path.join(OUT, "fixture.pdf").replace("\\", "/"))
    time.sleep(3.5)

    check("fit width is the default", js("S.zoomMode"), "width")

    def shortfall():
        """Unused width beside the page, in px. The stage pads 22px each side."""
        return js("(function(){"
                  "const v=document.getElementById('viewer');"
                  "const i=document.getElementById('pageimg');"
                  "return v.clientWidth - 44 - i.width;})()")

    small_zoom = js("S.zoom")
    check("page fills the window at first size", shortfall(),
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
w = webview.create_window("PDF Studio zoom", url=UI, js_api=api, width=1050, height=800)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
