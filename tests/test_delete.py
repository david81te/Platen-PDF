"""Delete must remove the selected object, whatever kind it is."""
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
from PIL import Image, ImageDraw

from backend import forms, signatures
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []

SRC = os.path.join(OUT, "delete_src.pdf")
_doc = fitz.open(os.path.join(OUT, "fixture.pdf"))
forms.add_field(_doc, 0, "text", [350, 470, 540, 492], "to_remove")
_doc.save(SRC)
_doc.close()

_png = os.path.join(OUT, "del_sig.png")
_img = Image.new("RGB", (300, 100), "white")
ImageDraw.Draw(_img).line([(20, 80), (270, 28)], fill=(12, 20, 90), width=8)
_img.save(_png)
SIG = signatures.add(_png, "ZZ Delete Test 5521")

# Every kind of object a person can put on a page.
KINDS = [
    ("circle", "annot_shape(0,'circle',[[100,300],[240,380]])"),
    ("rectangle", "annot_shape(0,'rect',[[100,300],[240,380]])"),
    ("arrow", "annot_shape(0,'arrow',[[100,300],[240,380]])"),
    ("line", "annot_shape(0,'line',[[100,300],[240,380]])"),
    ("freehand ink", "annot_ink(0,[[[100,300],[150,340],[200,310]]])"),
    ("sticky note", "annot_note(0,[300,300],'a note')"),
    ("text box", "annot_textbox(0,[100,300,300,340],'words')"),
    ("stamp", "annot_stamp(0,[100,300,300,350],'draft')"),
    ("highlight", "annot_markup(0,'highlight',[[100,180,400,200]])"),
]

CLICK_FIRST = ("if (document.querySelector('.annothit')) "
               "document.querySelector('.annothit').click()")


def drive(window):
    threading.Thread(target=lambda: (time.sleep(420), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-44s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.confirm = () => true;")

    def fresh():
        js("window.openOnStart(%s)" % repr(SRC.replace("\\", "/")))
        for _ in range(60):
            if js("S.info && S.info.page_count"):
                return
            time.sleep(0.2)

    def press(key, shift=False):
        js("document.dispatchEvent(new KeyboardEvent('keydown',{key:%s,"
           "shiftKey:%s,bubbles:true,cancelable:true}))"
           % (repr(key), "true" if shift else "false"))

    def place_and_select(call):
        fresh()
        js("window.pywebview.api." + call)
        time.sleep(1.6)
        js("setTool('select')")
        time.sleep(1.4)
        js(CLICK_FIRST)
        time.sleep(1.4)

    print("== the Delete key removes every kind of object ==")
    for label, call in KINDS:
        place_and_select(call)
        before = js("document.querySelectorAll('.annothit').length")
        selected = js("!!S.selectedAnnot")
        press("Delete")
        time.sleep(2.2)
        after = js("document.querySelectorAll('.annothit').length")
        ok = bool(before) and before >= 1 and selected and after == before - 1
        if not ok:
            fails.append((label, "before=%s selected=%s after=%s"
                          % (before, selected, after)))
        print("  %s %-44s %s" % ("ok  " if ok else "BUG ", label,
                                 "%s -> %s" % (before, after)), flush=True)

    print("")
    print("== Backspace does the same ==")
    place_and_select("annot_shape(0,'circle',[[100,300],[240,380]])")
    press("Backspace")
    time.sleep(2.2)
    check("backspace removes it",
          js("document.querySelectorAll('.annothit').length"), 0)

    print("")
    print("== and there is a visible button ==")
    place_and_select("annot_shape(0,'circle',[[100,300],[240,380]])")
    check("a shape shows a delete cross",
          js("!!document.querySelector('.selframe .kill')"), True)
    js("document.querySelector('.selframe .kill').click()")
    time.sleep(2.2)
    check("clicking it removes the shape",
          js("document.querySelectorAll('.annothit').length"), 0)

    place_and_select("annot_shape(0,'arrow',[[100,300],[240,380]])")
    check("an arrow shows one as well",
          js("!!document.querySelector('.linekill')"), True)
    js("document.querySelector('.linekill').click()")
    time.sleep(2.2)
    check("and it removes the arrow",
          js("document.querySelectorAll('.annothit').length"), 0)

    print("")
    print("== signatures and form fields ==")
    fresh()
    js("window.pywebview.api.sig_list().then(function (r) {"
       "  var s = r.data.filter(function (x) {"
       "    return x.name.indexOf('ZZ Delete Test') >= 0; })[0];"
       "  if (s) window.pywebview.api.sig_place(0, s.id, [120,600,320,660]); })")
    time.sleep(3.2)
    js("setTool('select')")
    time.sleep(1.4)
    js(CLICK_FIRST)
    time.sleep(1.4)
    press("Delete")
    time.sleep(2.2)
    check("a placed signature can be deleted",
          js("document.querySelectorAll('.annothit').length"), 0)

    fresh()
    js("setTool('select')")
    time.sleep(1.8)
    check("the form field is there",
          js("document.querySelectorAll('.fieldhit').length"), 1)
    js("S.selectedField = 'to_remove'")
    press("Delete")
    time.sleep(2.5)
    check("and Delete removes it",
          js("document.querySelectorAll('.fieldhit').length"), 0)

    print("")
    print("== Delete must not fire while typing ==")
    place_and_select("annot_shape(0,'circle',[[100,300],[240,380]])")
    js("openFind(); document.getElementById('findq').focus();")
    time.sleep(0.8)
    js("document.getElementById('findq').dispatchEvent("
       "new KeyboardEvent('keydown',{key:'Delete',bubbles:true,cancelable:true}))")
    time.sleep(1.8)
    check("typing in the find box leaves it alone",
          js("document.querySelectorAll('.annothit').length"), 1)

    print("")
    print("== arrow keys nudge the selection ==")
    place_and_select("annot_shape(0,'rect',[[100,300],[240,380]])")
    start = js("S.annots[0].rect.map(Math.round)")
    press("ArrowRight")
    press("ArrowRight")
    press("ArrowDown")
    time.sleep(2.6)
    check("arrows move it", js("S.annots[0] ? S.annots[0].rect.map(Math.round) : null"),
          lambda r: r and r != start)

    print("")
    print("== undo puts it back ==")
    place_and_select("annot_shape(0,'circle',[[100,300],[240,380]])")
    press("Delete")
    time.sleep(2.2)
    gone = js("document.querySelectorAll('.annothit').length")
    js("ACTIONS.undo()")
    time.sleep(2.6)
    js("setTool('select')")
    time.sleep(1.6)
    check("deleted then undone", "%s -> %s"
          % (gone, js("document.querySelectorAll('.annothit').length")), "0 -> 1")
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF delete", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

try:
    signatures.remove(SIG["id"])
except Exception:
    pass

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
