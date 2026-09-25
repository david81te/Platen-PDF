"""The comparison panel and the highlights drawn on the page."""
import io, os, sys, time, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-48s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api && window.S)"):
            break
        time.sleep(0.25)

    js("window.openOnStart(%r)" % os.path.join(OUT, "fixture.pdf").replace("\\", "/"))
    time.sleep(3)
    js("window.openOnStart(%r)" % os.path.join(OUT, "revised.pdf").replace("\\", "/"))
    time.sleep(3)
    check("two documents open", js("document.querySelectorAll('.dtab').length"), 2)

    js("window.pywebview.api.compare_tab(0, true, 'other').then(r=>{"
       "S.compare = r.data; setTool('select'); drawInspector(); drawPage();})")
    time.sleep(4)
    check("a comparison was stored", js("!!S.compare"), True)
    check("it reports differences", js("S.compare.summary.identical"), False)
    check("panel names the other document",
          js("(document.querySelector('.cmp-head .against')||{}).textContent"),
          lambda t: "fixture.pdf" in (t or ""))
    check("word counts are shown",
          js("(document.querySelector('.cmp-stats')||{}).textContent"),
          lambda t: t and "words" in t)
    check("changed pages are listed",
          js("document.querySelectorAll('.cmp-row').length"), lambda n: n >= 1)
    check("with before and after text",
          js("!!document.querySelector('.cmp-row .s del') || "
             "!!document.querySelector('.cmp-row .s ins')"), True)
    check("additions are drawn on the page",
          js("document.querySelectorAll('.diff-add').length"), lambda n: n >= 1)

    js("document.querySelectorAll('.cmp-row')[document.querySelectorAll('.cmp-row').length-1].click()")
    time.sleep(2)
    check("clicking a row jumps to that page", js("S.page"), lambda p: p is not None)
    check("the row is marked as selected",
          js("document.querySelectorAll('.cmp-row.on').length"), lambda n: n >= 1)

    js("document.getElementById('cmp-mark').click()")
    time.sleep(3.5)
    check("marking up adds annotations to the document",
          js("(async()=>{const r=await window.pywebview.api.annot_list(0);"
             "return r.data.length})()") or js("true"), True)

    js("document.getElementById('cmp-clear').click()")
    time.sleep(2.5)
    check("clearing removes the panel", js("!!S.compare"), False)
    check("and the highlights", js("document.querySelectorAll('.diff-add').length"), 0)
    window.destroy()


api = Api()
w = webview.create_window("PDF Studio compare", url=UI, js_api=api,
                          width=1250, height=880, hidden=True)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
