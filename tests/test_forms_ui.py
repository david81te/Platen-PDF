"""Forms must be usable from the window, not just from the API."""
import io, os, sys, time, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf as fitz
import webview
from backend import forms
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []

# a document with one of each field type
doc = fitz.open(os.path.join(OUT, "fixture.pdf"))
forms.add_field(doc, 0, "text", [350, 470, 540, 492], "signer_name")
forms.add_field(doc, 0, "checkbox", [350, 500, 366, 516], "agreed")
forms.add_field(doc, 0, "dropdown", [350, 524, 540, 546], "status",
                options=["Draft", "Final", "Signed"])
FORM = os.path.join(OUT, "formdoc.pdf")
doc.save(FORM)
doc.close()


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-46s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % FORM.replace("\\", "/"))
    time.sleep(3.5)
    js("setTool('select')")
    time.sleep(1.5)

    check("fields are clickable on the page",
          js("document.querySelectorAll('.fieldhit').length"), 3)
    check("select tool mentions them",
          js("document.getElementById('insp-body').textContent"),
          lambda t: "form field" in (t or ""))

    js("setTool('field')")
    time.sleep(1.5)
    check("the field tool lists every field",
          js("document.querySelectorAll('.frow').length"), 3)
    check("with a control for each",
          js("document.querySelectorAll('.frow input, .frow select').length"), 3)
    check("and the CSV buttons are there",
          js("!!document.getElementById('form-export') && "
             "!!document.getElementById('form-import')"), True)

    js("(function(){const i=document.getElementById('ff0');"
       "i.value='Ernie Willmore'; i.onchange();})()")
    time.sleep(2.5)
    check("typing a value stores it",
          js("(S.fields.find(f=>f.name==='signer_name')||{}).value"), "Ernie Willmore")

    js("(function(){const c=document.getElementById('ff1');"
       "c.checked=true; c.onchange();})()")
    time.sleep(2.5)
    check("ticking a checkbox stores it",
          js("(function(){const f=S.fields.find(f=>f.name==='agreed');"
             "return f && f.value && String(f.value).toLowerCase()!=='off';})()"), True)

    js("(function(){const s=document.getElementById('ff2');"
       "s.value='Signed'; s.onchange();})()")
    time.sleep(2.5)
    check("choosing from a dropdown stores it",
          js("(S.fields.find(f=>f.name==='status')||{}).value"), "Signed")

    js("setTool('select')")
    time.sleep(1.5)
    check("a ticked box shows a tick on the page",
          js("document.querySelectorAll('.fieldhit .tick').length"), 1)

    js("document.querySelectorAll('.fieldhit')[0].click()")
    time.sleep(1.2)
    check("clicking a text field opens an editor",
          js("!!document.querySelector('#modal input[name=v]')"), True)
    check("pre-filled with the current value",
          js("(document.querySelector('#modal input[name=v]')||{}).value"),
          "Ernie Willmore")
    js("document.querySelector('#modal [data-x]').click()")
    time.sleep(0.6)

    js("document.querySelectorAll('.fieldhit')[1].click()")
    time.sleep(2.5)
    check("clicking a checkbox toggles it with no dialog",
          js("(function(){const f=S.fields.find(f=>f.name==='agreed');"
             "return !(f && f.value && String(f.value).toLowerCase()!=='off');})()"), True)
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF forms", url=UI, js_api=api,
                          width=1250, height=880, hidden=True)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
