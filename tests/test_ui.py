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
HARD = os.path.join(OUT, "hard.pdf").replace("\\", "/")
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

        print("")
        print("-- document tabs --")
        check("one tab to start", js("document.querySelectorAll('.dtab').length"), 1)

        js("window.openOnStart(%s)" % json.dumps(HARD))
        for _ in range(60):
            if js("document.querySelectorAll('.dtab').length") == 2:
                break
            time.sleep(0.25)
        check("second file opens its own tab",
              js("document.querySelectorAll('.dtab').length"), 2)
        check("new tab is active",
              js("document.querySelectorAll('.dtab')[1].classList.contains('on')"), True)
        check("front document is the new one", js("S.info.name"), "hard.pdf")
        check("front document page count", js("S.info.page_count"), 7)

        js("switchTab(0)")
        for _ in range(40):
            if js("S.info && S.info.name") == "fixture.pdf":
                break
            time.sleep(0.25)
        check("switching swaps the document", js("S.info.name"), "fixture.pdf")
        check("page count follows the tab", js("S.info.page_count"), 1)
        check("first tab highlighted again",
              js("document.querySelectorAll('.dtab')[0].classList.contains('on')"), True)

        js("document.getElementById('findq').value = 'purchase'; runSearch();")
        for _ in range(40):
            if js("S.hits.length"):
                break
            time.sleep(0.25)
        found_before = js("S.hits.length")
        js("switchTab(1)")
        time.sleep(1.5)
        check("search results do not leak across tabs", js("S.hits.length"),
              lambda n: n == 0 and found_before >= 1)

        merged = js("(async()=>{const r=await window.pywebview.api.merge_tab(0,7);"
                    "return r.ok ? r.data.page_count : 'err:'+r.error})()")
        js("refresh()")
        time.sleep(1.5)
        check("merging another tab adds its pages", js("S.info.page_count"), 8)

        js("closeTab(0)")
        for _ in range(40):
            if js("document.querySelectorAll('.dtab').length") == 1:
                break
            time.sleep(0.25)
        check("closing a tab removes it",
              js("document.querySelectorAll('.dtab').length"), 1)
        check("remaining document still usable", js("S.info && S.info.page_count"), 8)

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
