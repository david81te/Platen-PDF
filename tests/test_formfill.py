"""Filling a form in: tick boxes, radio groups and typing on the page.

A radio group is the part worth being careful about. Its buttons are separate
widgets that all carry the same field name, so anything that looks a field up
by name finds the first button whatever was clicked - which does not fail, it
quietly records the wrong answer. PyMuPDF cannot create one, so this builds a
real radio group with pikepdf and works against that.
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402

import pikepdf  # noqa: E402
import pymupdf as fitz  # noqa: E402
import webview  # noqa: E402
from pikepdf import Array, Dictionary, Name, String  # noqa: E402

from backend import forms  # noqa: E402
from backend.api import Api  # noqa: E402
from backend.session import PdfError  # noqa: E402

OUT = os.path.join(ROOT, "tests", "output")
UI = os.path.join(ROOT, "ui", "index.html")
FORM = os.path.join(OUT, "radioform.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def build_fixture(path):
    """A page with a three-button radio group, two tick boxes, two text fields."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 320))

    def appearance(on_name, size=16):
        off = pdf.make_stream(b"0 0 0 RG 0.6 w 1 1 %d %d re S" % (size - 2, size - 2))
        off.Type, off.Subtype = Name.XObject, Name.Form
        off.BBox, off.Resources = Array([0, 0, size, size]), Dictionary()
        on = pdf.make_stream(b"0 0 0 RG 0.6 w 1 1 %d %d re S 0 0 0 rg "
                             b"4 4 m %d %d l S" % (size - 2, size - 2, size - 4, size - 4))
        on.Type, on.Subtype = Name.XObject, Name.Form
        on.BBox, on.Resources = Array([0, 0, size, size]), Dictionary()
        return Dictionary(N=Dictionary(**{on_name: on, "Off": off}))

    annots = []
    parent = pdf.make_indirect(Dictionary(
        FT=Name.Btn, T=String("colour"), Ff=32768, V=Name.Off, Kids=Array()))
    for label, y in [("Red", 250), ("Green", 220), ("Blue", 190)]:
        kid = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, Rect=Array([30, y, 46, y + 16]),
            Parent=parent, F=4, AP=appearance(label), AS=Name.Off, MK=Dictionary()))
        parent.Kids.append(kid)
        annots.append(kid)

    boxes = []
    for name, y in [("agree", 150), ("subscribe", 120)]:
        box = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, FT=Name.Btn, T=String(name),
            Rect=Array([30, y, 46, y + 16]), F=4, V=Name.Off, AS=Name.Off,
            AP=appearance("Yes"), MK=Dictionary()))
        annots.append(box)
        boxes.append(box)

    texts = []
    for name, y in [("full_name", 80), ("email", 50)]:
        txt = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String(name),
            Rect=Array([30, y, 300, y + 20]), F=4, V=String(""),
            DA=String("/Helv 11 Tf 0 g"), MK=Dictionary()))
        annots.append(txt)
        texts.append(txt)

    page.Annots = pdf.make_indirect(Array(annots))
    pdf.Root.AcroForm = pdf.make_indirect(Dictionary(
        Fields=Array([parent] + boxes + texts), DA=String("/Helv 0 Tf 0 g"),
        DR=Dictionary(Font=Dictionary()), NeedAppearances=True))
    pdf.save(path)


build_fixture(FORM)


def radios(doc):
    return [(f["on_state"], f["checked"]) for f in forms.listing(doc, 0)
            if f["kind"] == "radio"]


print("== what the page reports about itself ==")
doc = fitz.open(FORM)
listed = forms.listing(doc, 0)
check("every field is found", len(listed), 7)
check("three of them are radio buttons",
      sum(1 for f in listed if f["kind"] == "radio"), 3)
check("they share one field name",
      len({f["name"] for f in listed if f["kind"] == "radio"}), 1)
check("but each knows which option it is",
      sorted(f["on_state"] for f in listed if f["kind"] == "radio"),
      ["Blue", "Green", "Red"])
check("and each has its own identity",
      len({f["xref"] for f in listed if f["kind"] == "radio"}), 3)
check("a tick box knows its on name",
      next(f["on_state"] for f in listed if f["name"] == "agree"), "Yes")
check("a text field has no on name and is not ticked",
      [f["on_state"] for f in listed if f["name"] == "full_name"] +
      [f["checked"] for f in listed if f["name"] == "full_name"], [None, False])

print("")
print("== choosing a radio button chooses that one ==")
# The bug this guards: looked up by name, all three buttons are the first
# button, so clicking Blue recorded Red. Nothing failed; the answer was wrong.
blue = next(f for f in listed if f["on_state"] == "Blue")
green = next(f for f in listed if f["on_state"] == "Green")
red = next(f for f in listed if f["on_state"] == "Red")
check("nothing is chosen to begin with", radios(doc),
      [("Red", False), ("Green", False), ("Blue", False)])
forms.set_widget(doc, 0, blue["xref"], True)
check("choosing Blue chooses Blue, not the first one", radios(doc),
      [("Red", False), ("Green", False), ("Blue", True)])
forms.set_widget(doc, 0, green["xref"], True)
check("and moving to Green lets go of Blue", radios(doc),
      [("Red", False), ("Green", True), ("Blue", False)])
forms.set_widget(doc, 0, red["xref"], True)
check("and again to Red", radios(doc),
      [("Red", True), ("Green", False), ("Blue", False)])

print("")
print("== and it survives being saved ==")
saved = os.path.join(OUT, "radioform-saved.pdf")
doc.save(saved)
doc.close()
doc = fitz.open(saved)
check("the chosen button is still the chosen one", radios(doc),
      [("Red", True), ("Green", False), ("Blue", False)])
doc.close()
os.remove(saved)

print("")
print("== by name, which is how a CSV import arrives ==")
doc = fitz.open(FORM)
forms.set_value(doc, 0, "colour", "Green")
check("the named value picks the matching button", radios(doc),
      [("Red", False), ("Green", True), ("Blue", False)])
try:
    forms.set_value(doc, 0, "colour", "Purple")
    check("an option that does not exist is refused", False, "it was accepted")
except PdfError as exc:
    check("an option that does not exist is refused", "not one of the options" in str(exc), True)
    check("and the message says what the options are",
          all(c in str(exc) for c in ("Red", "Green", "Blue")), True)

print("")
print("== ticking and unticking ==")
agree = next(f for f in forms.listing(doc, 0) if f["name"] == "agree")
seen = []
for want in (True, False, True, False):
    seen.append(forms.set_widget(doc, 0, agree["xref"], want)["checked"])
check("a box goes on and off and on again", seen, [True, False, True, False])
other = next(f for f in forms.listing(doc, 0) if f["name"] == "subscribe")
forms.set_widget(doc, 0, agree["xref"], True)
check("one box does not drag the other with it",
      next(f["checked"] for f in forms.listing(doc, 0) if f["name"] == "subscribe"), False)
check("the one that was clicked is on",
      next(f["checked"] for f in forms.listing(doc, 0) if f["name"] == "agree"), True)

print("")
print("== typing into a text field ==")
name_field = next(f for f in forms.listing(doc, 0) if f["name"] == "full_name")
got = forms.set_widget(doc, 0, name_field["xref"], "Ada Lovelace")
check("the text lands", got["value"], "Ada Lovelace")
check("and it is not treated as a tick", got["checked"], False)
got = forms.set_widget(doc, 0, name_field["xref"], "")
check("and can be cleared again", got["value"], "")
doc.close()

print("")
print("== a field nobody may change ==")
doc = fitz.open(FORM)
page = doc[0]
locked = forms._find_xref(page, next(
    f["xref"] for f in forms.listing(doc, 0) if f["name"] == "email"))
locked.field_flags = locked.field_flags | 1
locked.update()
target = next(f["xref"] for f in forms.listing(doc, 0) if f["name"] == "email")
try:
    forms.set_widget(doc, 0, target, "nope@example.com")
    check("a read-only field is refused", False, "it was accepted")
except PdfError as exc:
    check("a read-only field is refused", "read only" in str(exc).lower(), True)
doc.close()


def drive(window):
    threading.Thread(target=lambda: (time.sleep(200), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % FORM.replace("\\", "/"))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(2.0)
    js("setTool('field')")
    time.sleep(2.0)

    print("")
    print("== from the window ==")
    check("every field can be clicked on the page",
          js("document.querySelectorAll('.fieldhit').length"), 7)
    check("nothing draws a tick of its own over the page's own one",
          js("document.querySelectorAll('.fieldhit .tick').length"), 0)

    def field_of(kind, on_state=None):
        return js("""(function () {
            const f = (S.fields || []).find(x => x.kind === %r %s);
            return f ? f.xref : null;
          })()""" % (kind, ("&& x.on_state === %r" % on_state) if on_state else ""))

    print("")
    print("== typing goes on the page, not into a dialog ==")
    text_xref = js("(S.fields.find(f => f.kind === 'text') || {}).xref")
    js("editField(S.fields.find(f => f.kind === 'text'))")
    time.sleep(1.2)
    check("a box appears on the page",
          js("!!document.querySelector('.fieldedit')"), True)
    check("and no dialog opened over the document",
          js("document.getElementById('modal-back').classList.contains('on')"), False)
    check("the box sits where the field is",
          js("""(function () {
            const b = document.querySelector('.fieldedit');
            const f = S.fields.find(x => x.xref === %s);
            if (!b || !f) return null;
            return Math.abs(parseFloat(b.style.left) - toPx(f.rect[0])) < 2
                && Math.abs(parseFloat(b.style.top) - toPx(f.rect[1])) < 2;
          })()""" % text_xref), True)
    check("it already has the keyboard",
          js("document.activeElement === document.querySelector('.fieldedit')"), True)

    js("""(function () {
        const b = document.querySelector('.fieldedit');
        b.value = 'Grace Hopper';
        b.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}));
      })()""")
    time.sleep(2.5)
    check("pressing Enter puts the text in the form",
          js("(S.fields.find(f => f.xref === %s) || {}).value" % text_xref),
          "Grace Hopper")
    check("and the box goes away",
          js("!!document.querySelector('.fieldedit')"), False)

    print("")
    print("== Tab moves to the next field ==")
    js("editField(S.fields.find(f => f.kind === 'text'))")
    time.sleep(1.2)
    js("""(function () {
        const b = document.querySelector('.fieldedit');
        b.dispatchEvent(new KeyboardEvent('keydown', {key:'Tab', bubbles:true, cancelable:true}));
      })()""")
    time.sleep(2.5)
    check("another field is now being filled in",
          js("S.selectedXref !== %s && !!S.selectedXref" % text_xref), True)
    js("if (S.editing) cancelEdit();")
    time.sleep(0.5)

    print("")
    print("== a tick box takes one click, with no dialog ==")
    box_xref = field_of("checkbox")
    js("editField(S.fields.find(f => f.xref === %s))" % box_xref)
    time.sleep(2.5)
    check("one click ticks it",
          js("(S.fields.find(f => f.xref === %s) || {}).checked" % box_xref), True)
    check("and no dialog was involved",
          js("document.getElementById('modal-back').classList.contains('on')"), False)
    js("editField(S.fields.find(f => f.xref === %s))" % box_xref)
    time.sleep(2.5)
    check("clicking again unticks it",
          js("(S.fields.find(f => f.xref === %s) || {}).checked" % box_xref), False)
    js("editField(S.fields.find(f => f.xref === %s))" % box_xref)
    time.sleep(2.5)
    check("and again re-ticks it",
          js("(S.fields.find(f => f.xref === %s) || {}).checked" % box_xref), True)

    print("")
    print("== clicking a radio button chooses that button ==")
    blue_x = field_of("radio", "Blue")
    js("editField(S.fields.find(f => f.xref === %s))" % blue_x)
    time.sleep(2.5)
    check("Blue is chosen",
          js("(S.fields.find(f => f.xref === %s) || {}).checked" % blue_x), True)
    check("and it is the only one",
          js("(S.fields || []).filter(f => f.kind === 'radio' && f.checked).length"), 1)
    check("the one chosen really is Blue",
          js("""((S.fields || []).find(f => f.kind === 'radio' && f.checked) || {})
                 .on_state"""), "Blue")

    red_x = field_of("radio", "Red")
    js("editField(S.fields.find(f => f.xref === %s))" % red_x)
    time.sleep(2.5)
    check("choosing Red lets go of Blue",
          js("""((S.fields || []).find(f => f.kind === 'radio' && f.checked) || {})
                 .on_state"""), "Red")
    check("still only one chosen",
          js("(S.fields || []).filter(f => f.kind === 'radio' && f.checked).length"), 1)

    print("")
    print("== the panel offers radios as radios ==")
    check("there is a radio control, not a text box",
          js("""document.querySelectorAll('#insp-body input[type="radio"]').length"""), 3)
    check("they are grouped by their field name",
          js("""new Set([...document.querySelectorAll('#insp-body input[type=\\"radio\\"]')]
                 .map(r => r.name)).size"""), 1)
    check("and the chosen one shows as chosen",
          js("""[...document.querySelectorAll('#insp-body input[type=\\"radio\\"]')]
                 .filter(r => r.checked).length"""), 1)

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF form filling", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
