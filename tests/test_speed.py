"""Opening and searching must stay quick on a document of real length."""
import io, os, sys, time, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
BIG = os.path.join(OUT, "big160.pdf").replace("\\", "/")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want, unit=""):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-44s %s%s" % ("ok  " if ok else "BUG ", label, got, unit), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    start = time.perf_counter()
    js("window.openOnStart(%r)" % BIG)
    for _ in range(200):
        if js("S.info && S.info.page_count") == 160 and js(
                "document.getElementById('pageimg').src.slice(0,10)") == "data:image":
            break
        time.sleep(0.1)
    opened = (time.perf_counter() - start) * 1000
    check("160-page document open and drawn", round(opened), lambda ms: ms < 4000, " ms")

    time.sleep(1.5)
    check("thumbnail slots exist for every page",
          js("document.querySelectorAll('#pane-thumbs .thumb').length"), 160)
    rendered = js("document.querySelectorAll('#pane-thumbs .thumb[data-done]').length")
    check("but only the visible ones are rendered", rendered,
          lambda n: 0 < n <= 40)

    start = time.perf_counter()
    js("document.getElementById('findq').value='agreement'; runSearch();")
    for _ in range(200):
        if js("S.hits.length"):
            break
        time.sleep(0.05)
    searched = (time.perf_counter() - start) * 1000
    check("search across 160 pages", round(searched), lambda ms: ms < 2500, " ms")
    check("and it found things", js("S.hits.length"), lambda n: n and n > 100)

    start = time.perf_counter()
    js("gotoPage(80)")
    for _ in range(200):
        if js("S.page") == 80:
            break
        time.sleep(0.05)
    check("jumping to page 81", round((time.perf_counter() - start) * 1000),
          lambda ms: ms < 1500, " ms")
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF speed", url=UI, js_api=api,
                          width=1250, height=880, hidden=True)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
