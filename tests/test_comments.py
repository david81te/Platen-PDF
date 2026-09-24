"""Comments: clicking one must open it, and selecting must not delete it."""
import os, sys, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webview
from backend.api import Api
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []

def drive(window):
    threading.Thread(target=lambda: (time.sleep(90), print("WATCHDOG"), os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok: fails.append((label, got))
        print("  %s %-42s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api && window.S)"): break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % os.path.join(OUT,"fixture.pdf").replace("\\","/"))
    time.sleep(3)

    js("window.pywebview.api.annot_note(0,[300,300],'Check this clause with counsel','Ernie')")
    time.sleep(1.5)
    js("setTool('select')")
    for _ in range(40):
        if js("document.querySelectorAll('.annothit').length"): break
        time.sleep(0.25)
    check("note is clickable on the page", js("document.querySelectorAll('.annothit').length"), lambda n: n >= 1)

    js("document.querySelector('.annothit').click()")
    time.sleep(1.5)
    check("clicking opens a bubble", js("!!document.querySelector('.bubble')"), True)
    check("bubble shows the comment", js("(document.querySelector('.bubble .txt')||{}).textContent"),
          lambda t: "counsel" in (t or ""))
    check("bubble shows the author", js("(document.querySelector('.bubble .who')||{}).textContent"),
          lambda t: "Ernie" in (t or ""))
    check("bubble offers edit and delete", js("document.querySelectorAll('.bubble .acts button').length"), 2)

    js("showPane('comments'); loadComments()")
    time.sleep(1.5)
    check("sidebar shows the full text", js("(document.querySelector('.cmt .body')||{}).textContent"),
          lambda t: "Check this clause with counsel" == (t or "").strip())
    check("selected comment highlighted", js("document.querySelectorAll('.cmt.on').length"), 1)
    js("document.querySelector('.cmt').click()")
    time.sleep(1.2)
    check("clicking a row does NOT delete it", js("document.querySelectorAll('.cmt').length"), 1)
    check("edit and delete are separate buttons", js("document.querySelectorAll('.cmt .acts button').length"), 2)

    js("window.pywebview.api.annot_update(0,S.annots[0].id,null,'Revised note text',null,null,null,'David').then(()=>loadComments())")
    time.sleep(2.2)
    check("editing updates the text", js("(document.querySelector('.cmt .body')||{}).textContent"),
          lambda t: "Revised note text" == (t or "").strip())

    js("window.pywebview.api.annot_list(0).then(async(r)=>{for(const x of r.data){await window.pywebview.api.annot_delete(0,x.id)} S.selectedAnnot=null; await refresh(false); await loadComments()})")
    time.sleep(3)
    check("deleting clears the sidebar", js("document.querySelectorAll('.cmt').length"), 0)
    check("and clears it from the page", js("document.querySelectorAll('.annothit').length"), 0)
    check("bubble is gone too", js("!!document.querySelector('.bubble')"), False)
    window.destroy()

api = Api()
w = webview.create_window("PDF Studio comments", url=UI, js_api=api, width=1200, height=800, hidden=True)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
