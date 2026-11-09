"""Turned text, edited through the window.

The text tool's click targets come from the page's text, which PyMuPDF
reports in the page's unrotated coordinates; on a turned page they landed
somewhere other than the words. And text running up a margin opened an
editing box as tall and thin as the text itself.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401

import pymupdf as fitz
import webview

from backend.api import Api

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
SRC = os.path.join(sandbox.DIR, "turned.pdf")

doc = fitz.open()
page = doc.new_page(width=612, height=792)
page.insert_text((150, 120), "Upright body text on the first page", fontsize=12)
where = fitz.Point(60, 600)
page.insert_text(where, "Label up the margin", fontsize=12, morph=(where, fitz.Matrix(0, 1, -1, 0, 0, 0)))
page = doc.new_page(width=612, height=792)
page.insert_text((72, 120), "A scanned landscape page", fontsize=12)
page.insert_text((72, 140), "Second line of the scan", fontsize=12)
page.set_rotation(90)
doc.save(SRC)
doc.close()
fails = []


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    def hits_on_page():
        return js("(function(){const img=document.getElementById('pageimg');"
                  "const W=img.width, H=img.height;"
                  "return [...document.querySelectorAll('.hit:not(.block)')].every(h => {"
                  "const x=parseFloat(h.style.left), y=parseFloat(h.style.top);"
                  "return x >= -1 && y >= -1 && x + h.offsetWidth <= W + 1 && y + h.offsetHeight <= H + 1;})"
                  " && document.querySelectorAll('.hit:not(.block)').length > 0;})()")

    def text_mode():
        js("setTool('select')")
        time.sleep(0.3)
        js("setTool('text')")
        for _ in range(40):
            if js("document.querySelectorAll('.hit').length"):
                return
            time.sleep(0.25)

    def edit(needle, text):
        shape = js("""(function(){
            for (const b of S.layout.blocks) for (const l of b.lines) for (const sp of l.spans)
              if (sp.text.includes(%r)) {
                editSpan(sp);
                const e = document.querySelector('.editor');
                const w = sp.bbox[2] - sp.bbox[0], h = sp.bbox[3] - sp.bbox[1];
                return {tall: h > w, editorWide: e.offsetWidth > e.offsetHeight, upright: sp.upright};
              }
            return null;})()""" % needle)
        js("document.querySelector('.editor').value = %r;"
           "document.querySelector('.editbar [data-save]').click()" % text)
        time.sleep(2.0)
        return shape

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % SRC.replace("\\", "/"))
    time.sleep(2.5)

    print("== text running up the margin of an upright page ==")
    text_mode()
    check("every click target is on the page", hits_on_page(), True)
    shape = edit("Label up", "Label up the margin, edited")
    check("the label is found, standing tall", shape and (shape["tall"], shape["upright"]),
          (True, False))
    check("but it is edited in a box you can type across", shape and shape["editorWide"], True)
    page = fitz.open(stream=api._session.doc.tobytes(), filetype="pdf")[0]
    lines = [("".join(s["text"] for s in l["spans"]), tuple(round(v, 2) for v in l["dir"]))
             for b in page.get_text("dict")["blocks"] for l in b["lines"]]
    check("the edit is saved, still running up the margin",
          ("Label up the margin, edited", (0.0, -1.0)) in lines, True)
    check("and the upright text is untouched",
          any(t == "Upright body text on the first page" for t, _ in lines), True)

    print("== a page turned 90 degrees ==")
    js("gotoPage(1)")
    time.sleep(2.0)
    text_mode()
    check("every click target is on the page as shown", hits_on_page(), True)
    shape = edit("scanned landscape", "An edited landscape page")
    check("the line reads down the screen, so it stands tall", shape and shape["tall"], True)
    check("and is edited in a box you can type across", shape and shape["editorWide"], True)
    text = fitz.open(stream=api._session.doc.tobytes(), filetype="pdf")[1].get_text()
    check("the edit is saved", "An edited landscape page" in text, True)
    check("and the next line kept", "Second line of the scan" in text, True)
    check("no errors shown", js("[...document.querySelectorAll('#toast .msg.err')].map(n => n.textContent)"), [])
    window.destroy()


api = Api()
w = webview.create_window("Platen PDF turned text", url=UI, js_api=api, width=1200, height=900)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
