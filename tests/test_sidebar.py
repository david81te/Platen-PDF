"""The right-hand panel folds away and gives its width to the document.

The width check is the point. A toggle that changes a class but leaves the
page drawn at the old width looks broken in exactly the way that makes people
stop using the button.
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webview  # noqa: E402

from backend.api import Api  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")
PDF = os.path.join(ROOT, "tests", "output", "fixture.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("try { localStorage.removeItem('inspectorCollapsed'); } catch (e) {}")
    js("restoreInspector()")
    time.sleep(0.5)

    print("== the button is there and says what it does ==")
    check("a toggle exists", js("!!document.getElementById('insp-toggle')"), True)
    check("it starts expanded",
          js("document.getElementById('inspector').classList.contains('collapsed')"), False)
    check("and announces that to a screen reader",
          js("document.getElementById('insp-toggle').getAttribute('aria-expanded')"), "true")
    check("its tooltip offers to hide",
          js("document.getElementById('insp-toggle').title"), "Hide panel")

    print("")
    print("== with a document open, collapsing widens the page ==")
    js("window.openOnStart(%s)" % repr(PDF.replace("\\", "/")))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(2.5)
    js("setZoom('width')")   # fit to width, so the page follows the stage
    time.sleep(1.5)

    panel_before = js("document.getElementById('inspector').getBoundingClientRect().width")
    page_before = js("document.getElementById('pageimg').getBoundingClientRect().width")
    js("document.getElementById('insp-toggle').click()")
    time.sleep(2.2)
    panel_after = js("document.getElementById('inspector').getBoundingClientRect().width")
    page_after = js("document.getElementById('pageimg').getBoundingClientRect().width")

    check("the panel is now collapsed",
          js("document.getElementById('inspector').classList.contains('collapsed')"), True)
    check("it shrank to a rail", panel_after, lambda w: w < panel_before and w <= 34)
    check("but is still visible, so it can be reopened", panel_after, lambda w: w > 10)
    check("the toggle is still reachable",
          js("document.getElementById('insp-toggle').getBoundingClientRect().width"),
          lambda w: w > 8)
    check("and the page took the freed width",
          round(page_after - page_before), lambda d: d > 100)
    check("the tooltip now offers to show",
          js("document.getElementById('insp-toggle').title"), "Show panel")
    check("aria-expanded followed",
          js("document.getElementById('insp-toggle').getAttribute('aria-expanded')"), "false")
    check("the panel body is hidden, not merely narrow",
          js("getComputedStyle(document.getElementById('insp-body')).display"), "none")

    print("")
    print("== and expanding puts it back ==")
    js("document.getElementById('insp-toggle').click()")
    time.sleep(2.2)
    check("expanded again",
          js("document.getElementById('inspector').classList.contains('collapsed')"), False)
    check("panel back to its width",
          js("document.getElementById('inspector').getBoundingClientRect().width"),
          lambda w: abs(w - panel_before) < 2)
    check("page back to its width",
          js("document.getElementById('pageimg').getBoundingClientRect().width"),
          lambda w: abs(w - page_before) < 3)

    print("")
    print("== the choice is remembered ==")
    js("document.getElementById('insp-toggle').click()")
    time.sleep(1.2)
    check("collapsing was written down",
          js("localStorage.getItem('inspectorCollapsed')"), "1")
    # Re-running the restore is what a fresh launch does.
    js("document.getElementById('inspector').classList.remove('collapsed'); restoreInspector()")
    time.sleep(0.8)
    check("a fresh start comes back collapsed",
          js("document.getElementById('inspector').classList.contains('collapsed')"), True)
    js("document.getElementById('insp-toggle').click()")
    time.sleep(1.2)
    check("and expanding is remembered too",
          js("localStorage.getItem('inspectorCollapsed')"), "0")

    print("")
    print("== blocked storage does not break the button ==")
    js("""window.__realSet = localStorage.setItem;
          localStorage.setItem = function () { throw new Error('blocked'); };""")
    errored = js("""(function () {
        try { setInspector(true, false); return false; } catch (e) { return true; }
      })()""")
    check("toggling still works when storage throws", errored, False)
    check("and it did collapse",
          js("document.getElementById('inspector').classList.contains('collapsed')"), True)
    js("localStorage.setItem = window.__realSet; localStorage.removeItem('inspectorCollapsed');")

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF sidebar", url=UI, js_api=api,
                          width=1250, height=880, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
