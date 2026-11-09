"""The page number, header and footer, and Bates dialogs, driven in the window."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401  - keeps settings out of the real folder

import pymupdf as fitz
import webview

from backend import prefs
from backend.api import Api

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
SRC = os.path.join(sandbox.DIR, "stamps.pdf")
doc = fitz.open()
for n in range(3):
    doc.new_page().insert_text((72, 100), "Body of page %d" % (n + 1), fontsize=12)
doc.save(SRC)
doc.close()
prefs.set("bates", {})
fails = []


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    def page_text():
        return [p.get_text() for p in fitz.open(stream=api._session.doc.tobytes(), filetype="pdf")]

    def fits():
        return js("(function(){const m=document.getElementById('modal');"
                  "return m.scrollWidth <= m.clientWidth + 1 && "
                  "[...m.querySelectorAll('input,select')].every(i => i.getBoundingClientRect().right "
                  "<= m.getBoundingClientRect().right + 1);})()")

    def ok():
        js("document.querySelector('#modal [data-ok]').click()")
        time.sleep(1.5)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % SRC.replace("\\", "/"))
    time.sleep(2.5)

    print("== page numbers ==")
    js("ACTIONS.numbers()")
    time.sleep(0.5)
    check("the dialog fits", fits(), True)
    js("(function(){const s=document.querySelector('#modal [name=preset]');"
       "s.selectedIndex=1; s.onchange({target:s});})()")
    check("a style fills in the format",
          js("document.querySelector('#modal [name=template]').value"), "Page {page}")
    js("document.querySelector('#modal [name=position]').value='top-right'")
    ok()
    check("numbers every page", ["Page %d" % n in t for n, t in enumerate(page_text(), 1)],
          [True, True, True])

    js("ACTIONS.numbers()")
    time.sleep(0.5)
    ok()
    check("applying again replaces them", [t.count("Page ") for t in page_text()], [1, 1, 1])

    print("== header and footer ==")
    js("ACTIONS.headfoot()")
    time.sleep(0.5)
    check("the dialog fits", fits(), True)
    check("all six positions offered", js("document.querySelectorAll('#modal .field3 input').length"), 6)
    js("document.querySelector('#modal [name=\"top-center\"]').value='CONFIDENTIAL';"
       "document.querySelector('#modal [name=\"bottom-left\"]').value='{file}'")
    ok()
    texts = page_text()
    check("header on every page", ["CONFIDENTIAL" in t for t in texts], [True] * 3)
    check("footer names the file", ["stamps.pdf" in t for t in texts], [True] * 3)
    check("page numbers untouched", ["Page " in t for t in texts], [True] * 3)

    print("== Bates numbering ==")
    js("ACTIONS.bates()")
    time.sleep(0.8)
    check("the dialog fits", fits(), True)
    js("(function(){const f=(n)=>document.querySelector('#modal [name='+n+']');"
       "f('prefix').value='WCP'; f('start').value='7'; f('digits').value='4';"
       "f('prefix').dispatchEvent(new Event('input'));})()")
    check("the preview shows the range",
          js("document.getElementById('bates-preview').textContent"),
          "WCP0007 to WCP0009 (3 pages); the next set would start at WCP0010.")
    ok()
    check("numbered", ["WCP%04d" % n in t for n, t in zip(range(7, 10), page_text())],
          [True] * 3)
    check("where it stopped is remembered", (prefs.get("bates") or {}).get("next"), 10)
    js("ACTIONS.bates()")
    time.sleep(0.8)
    check("the next set carries on from there",
          (js("document.querySelector('#modal [name=start]').value"),
           js("document.querySelector('#modal [name=prefix]').value")), ("10", "WCP"))
    js("document.querySelector('#modal [data-remove]').click()")
    time.sleep(1.5)
    check("remove takes the Bates numbers", ["WCP" in t for t in page_text()], [False] * 3)
    check("and only those", ["CONFIDENTIAL" in t and "Page " in t for t in page_text()],
          [True] * 3)

    print("== remove from the page number dialog ==")
    js("ACTIONS.numbers()")
    time.sleep(0.5)
    js("document.querySelector('#modal [data-remove]').click()")
    time.sleep(1.5)
    check("page numbers gone", ["Page " in t for t in page_text()], [False] * 3)
    check("header kept", ["CONFIDENTIAL" in t for t in page_text()], [True] * 3)
    check("no errors shown", js("[...document.querySelectorAll('#toast .msg.err')].length"), 0)
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF stamps", url=UI, js_api=api, width=1200, height=850)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
