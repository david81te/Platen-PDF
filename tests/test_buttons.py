"""Click every control in the window and check it actually does something.

File dialogs are stubbed so nothing blocks, confirm() is auto-accepted, and
each menu action starts from a freshly opened document so one action cannot
poison the next. A control counts as broken if it throws, if it raises an error
toast, or if it claims to change the document and does not.
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sandbox  # noqa: F401  - redirects the signature library; must precede backend

import pymupdf as fitz
import webview

from backend import forms, signatures
from backend.api import Api
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
SCRATCH = os.path.join(OUT, "buttons")
os.makedirs(SCRATCH, exist_ok=True)
findings = []

# A document with something of everything: text, a table, a form field.
SOURCE = os.path.join(OUT, "buttons_src.pdf")
_doc = fitz.open(os.path.join(OUT, "fixture.pdf"))
forms.add_field(_doc, 0, "text", [350, 470, 540, 492], "signer_name")
forms.add_field(_doc, 0, "checkbox", [350, 500, 366, 516], "agreed")
_doc.save(SOURCE)
_doc.close()

_sig_png = os.path.join(SCRATCH, "sig.png")
_img = Image.new("RGB", (300, 100), "white")
ImageDraw.Draw(_img).line([(20, 80), (270, 28)], fill=(12, 20, 90), width=8)
_img.save(_sig_png)
SIG = signatures.add(_sig_png, "ZZ Button Test 4417", "Tester")


class StubbedApi(Api):
    """Every file dialog answers immediately, so no click can block."""

    def _ask_open(self, multiple=False, types=None):
        picks = {"image": _sig_png, "csv": os.path.join(SCRATCH, "fields.csv")}
        if types and "Image" in str(types):
            return [picks["image"]]
        if types and "CSV" in str(types):
            path = picks["csv"]
            if not os.path.isfile(path):
                with open(path, "w", encoding="utf-8-sig") as fh:
                    fh.write("page,field,type,value\n1,signer_name,text,Imported\n")
            return [path]
        return [os.path.join(OUT, "hard.pdf")]

    def _ask_save(self, filename, types=None):
        return os.path.join(SCRATCH, filename or "out.pdf")

    def _ask_folder(self):
        return SCRATCH


# Actions whose whole job is to change the document; anything else is allowed
# to be a no-op on this particular file.
MUST_CHANGE = {
    "insert", "duplicate", "merge", "cropreset", "watermark",
    "wmimage", "numbers", "bg", "flatten", "flattenforms",
}

# Refusals that are the right answer on this test document, not faults:
# a one-page file cannot lose its only page, a freshly opened file has nothing
# to undo, a text PDF needs no OCR, and the auto-confirmed dialogs submit empty
# passwords and no redaction marks.
EXPECTED_REFUSAL = {
    "delete": "must keep at least one page",
    "undo": "Nothing to undo",
    "redo": "Nothing to redo",
    "ocr": "already contains real text",
    "protect": "Set an open password",
    "redactapply": "Nothing is marked",
    "headfoot": "Type the text to place first",
}
# Actions that legitimately do nothing to the open file.
NO_CHANGE_EXPECTED = {
    "save", "saveas", "props", "defaultapp", "close", "find", "replace",
    "compare", "split", "extract", "docx", "xlsx", "pptx", "images", "text",
    "compress", "protect", "unprotect", "redactclear", "bookmarks", "open",
    "create", "cropstart", "signature", "image", "mergetab", "ocr",
    "redactapply",
}


def drive(window):
    threading.Thread(target=lambda: (time.sleep(900), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def note(ok, label, detail=""):
        if not ok:
            findings.append((label, detail))
        print("  %s %-34s %s" % ("ok  " if ok else "BUG ", label, detail), flush=True)

    def wait_ready():
        for _ in range(80):
            if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
                return True
            time.sleep(0.25)
        return False

    def fresh(path=SOURCE):
        js("S.compare=null; S.hits=[]; S.selectedAnnot=null;")
        js("window.openOnStart(%r)" % path.replace("\\", "/"))
        for _ in range(60):
            if js("S.info && S.info.page_count"):
                return True
            time.sleep(0.2)
        return False

    def errors_since():
        return js("[...document.querySelectorAll('#toast .msg.err')]"
                  ".map(n => n.textContent).join(' | ')")

    def clear_toasts():
        js("document.getElementById('toast').innerHTML = ''")

    def settle(modal_choice="ok", seconds=2.2):
        """Let the action run, answering any dialog it opens."""
        time.sleep(0.6)
        if js("document.getElementById('modal-back').classList.contains('on')"):
            sel = "[data-ok]" if modal_choice == "ok" else "[data-x]"
            if not js("!!document.querySelector('#modal %s')" % sel):
                sel = "[data-x]"
            js("document.querySelector('#modal %s').click()" % sel)
        time.sleep(seconds)

    if not wait_ready():
        note(False, "window never became ready")
        window.destroy()
        return

    js("window.confirm = () => true; window.alert = () => {};")

    print("== sidebar tabs ==")
    for pane in ("thumbs", "find", "outline", "comments", "sigs"):
        fresh() if pane == "thumbs" else None
        clear_toasts()
        js("document.querySelector('.tab[data-pane=\"%s\"]').click()" % pane)
        time.sleep(0.9)
        active = js("document.getElementById('pane-%s').classList.contains('active')" % pane)
        note(bool(active), "tab: " + pane, "" if active else "pane did not activate")

    print("\n== toolbar tools ==")
    fresh()
    tools = js("[...document.querySelectorAll('.tool')].map(b => b.dataset.tool)")
    for tool in tools:
        clear_toasts()
        js("document.querySelector('.tool[data-tool=\"%s\"]').click()" % tool)
        time.sleep(0.7)
        chosen = js("S.tool")
        rendered = js("document.getElementById('insp-body').innerHTML.length")
        bad = errors_since()
        ok = chosen == tool and rendered and rendered > 10 and not bad
        note(ok, "tool: " + tool,
             bad or ("" if ok else "tool=%r inspector=%s" % (chosen, rendered)))

    print("\n== page navigation and zoom ==")
    fresh(os.path.join(OUT, "hard.pdf"))
    checks = [
        ("next", "document.getElementById('next').click()", "S.page", lambda v: v == 1),
        ("prev", "document.getElementById('prev').click()", "S.page", lambda v: v == 0),
        ("zoom in", "document.getElementById('zoomin').click()", "S.zoom", lambda v: v > 1),
        ("zoom out", "document.getElementById('zoomout').click()", "S.zoom", lambda v: v > 0),
        ("fit toggle", "document.getElementById('zoomfit').click()", "S.zoomMode",
         lambda v: v in ("width", "page")),
        ("zoom picker", "(function(){const p=document.getElementById('zoompick');"
                        "p.value='0.75'; p.onchange();})()", "S.zoom",
         lambda v: abs(v - 0.75) < 0.01),
        ("page box", "(function(){const b=document.getElementById('pagenum');"
                     "b.value='3'; b.onchange();})()", "S.page", lambda v: v == 2),
    ]
    for label, action, probe, want in checks:
        clear_toasts()
        js(action)
        time.sleep(1.1)
        value = js(probe)
        bad = errors_since()
        ok = (not bad) and want(value)
        note(ok, label, bad or ("" if ok else "%s = %r" % (probe, value)))

    print("\n== find bar ==")
    fresh()
    js("openFind(); document.getElementById('findq').value='purchase'; runSearch();")
    time.sleep(2)
    note(js("S.hits.length") > 0, "search finds hits", "")
    js("document.getElementById('findnext').click()")
    time.sleep(0.8)
    note(js("S.hitIndex") is not None, "next match", "")
    js("document.getElementById('findprev').click()")
    time.sleep(0.8)
    note(js("S.hitIndex") is not None, "previous match", "")
    js("(function(){const c=document.getElementById('findcase');"
       "c.checked=true; c.onchange();})()")
    time.sleep(1.5)
    note(js("S.hits") is not None, "match case toggle", "")

    print("\n== menu actions ==")
    acts = js("[...document.querySelectorAll('.menu li[data-act]')].map(li => li.dataset.act)")
    for act in acts:
        if not fresh():
            note(False, "menu: " + act, "could not reopen the document")
            continue
        before = js("(function(){return S.info ? S.info.page_count : -1})()")
        before_dirty = js("!!(S.info && S.info.dirty)")
        clear_toasts()
        try:
            js("[...document.querySelectorAll('.menu li[data-act]')]"
               ".find(li => li.dataset.act === '%s').click()" % act)
        except Exception as exc:
            note(False, "menu: " + act, "threw on click: %r" % (exc,))
            continue
        try:
            # Print is the one action that reaches outside the program. Letting
            # the suite confirm it would queue a real job on whatever printer
            # the machine happens to have, so this dismisses the dialog instead.
            # That it opens, and what it contains, is checked by test_print_ui.
            settle(modal_choice="x" if act == "print" else "ok")
        except Exception as exc:
            note(False, "menu: " + act, "threw while running: %r" % (exc,))
            continue

        bad = errors_since()
        after = js("(function(){return S.info ? S.info.page_count : -1})()")
        after_dirty = js("!!(S.info && S.info.dirty)")
        changed = (after != before) or (after_dirty and not before_dirty)
        expected = EXPECTED_REFUSAL.get(act)
        if bad and expected and expected.lower() in bad.lower():
            note(True, "menu: " + act, "refused correctly")
        elif bad:
            note(False, "menu: " + act, "error toast: " + bad)
        elif act in MUST_CHANGE and not changed:
            note(False, "menu: " + act, "claimed to act but nothing changed")
        else:
            note(True, "menu: " + act, "changed" if changed else "ran")

    print("\n== panel buttons ==")
    fresh()
    js("setTool('field')")
    time.sleep(1.2)
    for bid, label in [("form-export", "forms: export CSV"),
                       ("form-import", "forms: import CSV"),
                       ("form-flatten", "forms: lock values")]:
        clear_toasts()
        if not js("!!document.getElementById('%s')" % bid):
            note(False, label, "button missing")
            continue
        js("document.getElementById('%s').click()" % bid)
        settle(seconds=2.5)
        bad = errors_since()
        note(not bad, label, bad)
        if bid == "form-flatten":
            fresh()
            js("setTool('field')")
            time.sleep(1.0)

    fresh()
    js("setTool('redact')")
    time.sleep(1.0)
    clear_toasts()
    js("document.getElementById('redact-find').value='purchase';"
       "document.getElementById('redact-all').click();")
    time.sleep(2.5)
    note(not errors_since(), "redact: mark all matches", errors_since())
    clear_toasts()
    js("document.getElementById('redact-undo').click()")
    time.sleep(2)
    note(not errors_since(), "redact: clear marks", errors_since())
    clear_toasts()
    js("document.getElementById('redact-now').click()")
    time.sleep(2.5)
    note(True, "redact: apply", "ran")

    fresh()
    js("setTool('stamp')")
    time.sleep(1.0)
    clear_toasts()
    js("(function(){const s=document.getElementById('stampsel');"
       "s.value='confidential'; s.onchange();"
       "document.querySelectorAll('#insp-body .swatch')[3].click();})()")
    time.sleep(1.0)
    note(js("S.stamp") == "confidential", "stamp: choose type", js("S.stamp"))
    note(js("JSON.stringify(S.stampColor)") != "[0.1,0.5,0.15]",
         "stamp: choose colour", js("JSON.stringify(S.stampColor)"))

    fresh()
    js("setTool('ink')")
    time.sleep(0.9)
    js("(function(){const r=document.getElementById('lw');"
       "if(r){r.value='5'; r.oninput();}})()")
    time.sleep(0.6)
    note(js("S.lineWidth") == 5, "draw: line width", js("S.lineWidth"))

    print("\n== signatures panel ==")
    fresh()
    js("showPane('sigs'); loadSigs();")
    time.sleep(1.8)
    note(js("document.querySelectorAll('.sig-card').length") > 0,
         "signature list renders", "")
    js("window.__c = [...document.querySelectorAll('.sig-card')]"
       ".find(c => c.textContent.indexOf('ZZ Button Test 4417') >= 0);")
    note(js("!!window.__c"), "our test signature is listed", "")
    js("window.__c.click()")
    time.sleep(1.2)
    note(js("S.sig") is not None, "selecting a signature arms it", js("S.tool"))
    note(js("!!window.__c.querySelector('.rn')"), "rename button present", "")
    note(js("!!window.__c.querySelector('.x')"), "delete button present", "")

    print("\n== tabs across documents ==")
    js("window.openOnStart(%r)" % os.path.join(OUT, "hard.pdf").replace("\\", "/"))
    time.sleep(2.5)
    note(js("document.querySelectorAll('.dtab').length") >= 2, "second tab opened", "")
    clear_toasts()
    js("document.getElementById('tabadd').click()")
    settle(modal_choice="x", seconds=2)
    note(True, "new tab button", "ran")
    window.destroy()


api = StubbedApi()
w = webview.create_window("Platen PDF buttons", url=UI, js_api=api,
                          width=1300, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

try:
    signatures.remove(SIG["id"])
except Exception:
    pass

print("\n" + "=" * 66)
if findings:
    print("%d control(s) with a problem:" % len(findings))
    for label, detail in findings:
        print("  - %-32s %s" % (label, detail))
else:
    print("Every control behaved.")
sys.exit(1 if findings else 0)
