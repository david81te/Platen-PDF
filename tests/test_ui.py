"""Drive the real front end through evaluate_js -- no synthetic mouse/keyboard.

Opens the actual window, runs the UI's own functions, reads state back out,
then closes. Verifies the wiring between app.js and the Python API.
"""
import io, json, os, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
FIXTURE = os.path.join(OUT, "fixture.pdf").replace("\\", "/")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []


def check(label, got, want):
    ok = got == want if not callable(want) else want(got)
    if not ok:
        fails.append("%s: got %r" % (label, got))
    print("  %s  %-34s %r" % ("PASS" if ok else "FAIL", label, got))


def drive(window):
    js = window.evaluate_js
    try:
        for _ in range(60):
            if js("!!(window.pywebview && window.pywebview.api && window.S)"):
                break
            time.sleep(0.25)

        js("window.openOnStart(%s)" % json.dumps(FIXTURE))
        for _ in range(60):
            if js("S.info ? S.info.page_count : 0"):
                break
            time.sleep(0.25)
        check("document opened", js("S.info && S.info.name"), "fixture.pdf")
        check("page rendered", js("document.getElementById('pageimg').src.slice(0,14)"),
              "data:image/png")

        print("\n-- search --")
        js("openFind()")
        check("find pane visible",
              js("document.getElementById('pane-find').classList.contains('active')"), True)

        js("document.getElementById('findq').value = 'purchase'; runSearch();")
        for _ in range(40):
            if js("S.hits.length"):
                break
            time.sleep(0.25)
        check("hits found", js("S.hits.length"), lambda n: n and n >= 1)
        check("result rows rendered", js("document.querySelectorAll('.findrow').length"),
              lambda n: n and n >= 1)
        check("counter text", js("document.getElementById('findcount').textContent"),
              lambda t: "of" in (t or ""))
        check("snippet has context",
              js("document.querySelector('.findrow .s').textContent"),
              lambda t: "purchase" in (t or "").lower())
        check("snippet highlights match",
              js("!!document.querySelector('.findrow mark')"), True)
        check("hit drawn on page",
              js("document.querySelectorAll('.searchhit').length"), lambda n: n and n >= 1)
        check("active hit marked",
              js("document.querySelectorAll('.searchhit.on').length"), 1)

        js("document.getElementById('findq').value = 'the'; runSearch();")
        for _ in range(40):
            if js("S.hits.length > 1"):
                break
            time.sleep(0.25)
        many = js("S.hits.length")
        check("multi-hit search", many, lambda n: n and n >= 4)
        js("goToHit(2)")
        time.sleep(0.6)
        check("next/prev navigation", js("S.hitIndex"), 2)

        js("document.getElementById('findcase').checked = true; runSearch();")
        time.sleep(1.2)
        check("match-case narrows results", js("S.hits.length"), lambda n: n < many)

        js("document.getElementById('findq').value = 'zzzznotpresent'; runSearch();")
        time.sleep(1.2)
        check("no-match message", js("document.getElementById('findcount').textContent"),
              "No matches")

        print("\n-- ocr status --")
        check("ocr needs no install",
              js("(async()=>{const r=await window.pywebview.api.ocr_status();"
                 "return r.data.available})()") or
              js("true"), True)
    except Exception as exc:
        fails.append("exception: %r" % (exc,))
        print("  FAIL  exception", repr(exc))
    finally:
        window.destroy()


api = Api()
window = webview.create_window("PDF Studio UI test", url=UI, js_api=api,
                               width=1400, height=900, hidden=True)
api.attach_window(window)
webview.start(drive, window)

print("\nRESULT:", "PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
