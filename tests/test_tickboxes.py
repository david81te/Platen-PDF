"""Ticking a square that is only a drawing.

Plenty of forms are printed rather than built: the little square is a line
drawing or the character U+2610, with no form field behind it, so no reader
lets you tick it.

Most of this is about what must NOT be found. A page where every small
rectangle has become clickable is worse than one where nothing is, so the
fixture is full of things that look a bit like tick boxes and are not: table
cells, a signature rule, filled bullets, and a box far too big.
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

import sandbox  # noqa: E402

import pymupdf as fitz  # noqa: E402
import webview  # noqa: E402

from backend import boxes  # noqa: E402
from backend.api import Api  # noqa: E402

OUT = os.path.join(ROOT, "tests", "output")
UI = os.path.join(ROOT, "ui", "index.html")
FLAT = os.path.join(sandbox.DIR, "flatform.pdf")
GLYPH = os.path.join(sandbox.DIR, "glyphform.pdf")
SYMBOL_FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                           "Fonts", "seguisym.ttf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def build_flat(path):
    """A printed form. Each group gets its own shape, or finish() would apply
    the bullets' black fill to every square drawn before them."""
    doc = fitz.open()
    page = doc.new_page(width=460, height=620)
    page.insert_text(fitz.Point(40, 44), "Membership application", fontsize=15)
    wanted = []

    s = page.new_shape()
    for label, y in [("Individual", 90), ("Family", 118), ("Student", 146),
                     ("Corporate", 174), ("Life member", 202)]:
        box = fitz.Rect(44, y, 56, y + 12)
        s.draw_rect(box)
        wanted.append(box)
        page.insert_text(fitz.Point(66, y + 10), label, fontsize=10)
    big = fitz.Rect(44, 236, 62, 254)
    s.draw_rect(big)
    wanted.append(big)
    page.insert_text(fitz.Point(72, 249), "I agree to the terms", fontsize=10)
    s.finish(color=(0, 0, 0), width=0.8)
    s.commit()

    s = page.new_shape()
    page.insert_text(fitz.Point(46, 294), "Previous addresses", fontsize=10)
    for row in range(3):
        for col in range(3):
            s.draw_rect(fitz.Rect(44 + col * 120, 300 + row * 26,
                                  44 + col * 120 + 115, 300 + row * 26 + 24))
    s.draw_rect(fitz.Rect(44, 400, 400, 416))          # signature rule
    page.insert_text(fitz.Point(44, 430), "Signature", fontsize=10)
    s.draw_rect(fitz.Rect(44, 520, 400, 600))          # notes box
    page.insert_text(fitz.Point(50, 516), "Notes", fontsize=10)
    s.finish(color=(0, 0, 0), width=0.8)
    s.commit()

    s = page.new_shape()
    for n in range(3):
        s.draw_rect(fitz.Rect(44, 450 + n * 18, 49, 455 + n * 18))
        page.insert_text(fitz.Point(58, 455 + n * 18), "a bullet point", fontsize=9)
    s.finish(fill=(0, 0, 0), color=(0, 0, 0), width=0.5)
    s.commit()

    doc.save(path)
    doc.close()
    return [tuple(round(v, 1) for v in r) for r in wanted]


def build_glyph(path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_font(fontname="sym", fontfile=SYMBOL_FONT)
    for n, label in enumerate(("Yes", "No", "Maybe")):
        page.insert_text(fitz.Point(40, 60 + n * 35), "\u2610 " + label,
                         fontsize=14, fontname="sym")
    doc.save(path)
    doc.close()


EXPECTED = build_flat(FLAT)

print("== a printed form, with plenty of things that are not tick boxes ==")
doc = fitz.open(FLAT)
found = boxes.find(doc, 0)
check("every tick box is found", len(found), len(EXPECTED))
check("and they are the right ones",
      [tuple(round(v, 1) for v in b["rect"]) for b in found], sorted(EXPECTED))
check("none of them is ticked yet", [b["ticked"] for b in found],
      lambda t: not any(t))
check("they come back in reading order",
      [round(b["rect"][1]) for b in found],
      lambda ys: ys == sorted(ys))

print("")
print("== and nothing that is not one ==")
# Each of these appears on the fixture page and must be left alone.
rects = [b["rect"] for b in found]


def any_near(w, h):
    return any(abs((r[2] - r[0]) - w) < 1 and abs((r[3] - r[1]) - h) < 1
               for r in rects)


check("a table cell is not a tick box", any_near(115, 24), False)
check("a signature rule is not a tick box", any_near(356, 16), False)
check("a big notes box is not a tick box", any_near(356, 80), False)
check("a filled bullet is not a tick box", any_near(5, 5), False)
check("nothing wider than a tick box got through",
      max((r[2] - r[0]) for r in rects), lambda w: w <= boxes.MAX_SIDE)
check("nor anything narrower",
      min((r[2] - r[0]) for r in rects), lambda w: w >= boxes.MIN_SIDE)

print("")
print("== ticking and clearing ==")
third = found[2]["rect"]
got = boxes.toggle(doc, 0, third)
check("one click ticks it", got["ticked"], True)
check("and the page says so",
      [b["ticked"] for b in boxes.find(doc, 0)], [False, False, True, False, False, False])
got = boxes.toggle(doc, 0, third)
check("clicking again clears it", got["ticked"], False)
check("and it took exactly one tick away", got["removed"], 1)
check("the page agrees",
      [b["ticked"] for b in boxes.find(doc, 0)], lambda t: not any(t))

print("")
print("== a tick is real ink, not a note on the side ==")


def ink(rect):
    pix = doc[0].get_pixmap(dpi=120, clip=fitz.Rect(*rect))
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if sum(pix.pixel(x, y)) / 3 < 150)


# The same square before and after. An empty box is not blank - it has its own
# outline - so the only honest measure is how much the tick adds to it.
target = found[0]["rect"]
before = ink(target)
boxes.toggle(doc, 0, target)
after = ink(target)
check("the square has its outline to begin with", before, lambda n: n > 0)
check("and ticking it puts noticeably more ink in", after - before,
      lambda n: n > before * 0.4)

# It must stay inside its box. A tick hanging out would sit over the label
# next to it, so the strip of page immediately to the right stays as it was.
right_of = [target[2] + 1, target[1], target[2] + 9, target[3]]
check("and the tick does not spill out beside the label", ink(right_of), 0)

print("")
print("== it survives being saved ==")
for b in (found[1], found[4]):
    boxes.toggle(doc, 0, b["rect"])
saved = os.path.join(sandbox.DIR, "flatform-ticked.pdf")
doc.save(saved)
doc.close()
doc = fitz.open(saved)
check("the ticks are still there after a reopen",
      [b["ticked"] for b in boxes.find(doc, 0)],
      [True, True, False, False, True, False])
doc.close()

print("")
print("== a typed ballot box counts too ==")
if os.path.isfile(SYMBOL_FONT):
    build_glyph(GLYPH)
    doc = fitz.open(GLYPH)
    typed = boxes.find(doc, 0)
    check("the character is found as a box", len(typed), 3)
    check("squared off, so a tick lands inside it",
          [round(b["rect"][2] - b["rect"][0], 1) == round(b["rect"][3] - b["rect"][1], 1)
           for b in typed], lambda t: all(t))
    boxes.toggle(doc, 0, typed[0]["rect"])
    check("and it ticks", [b["ticked"] for b in boxes.find(doc, 0)],
          [True, False, False])
    doc.close()
else:
    print("  SKIP no symbol font on this machine to typeset a ballot box")

print("")
print("== a real form is left alone ==")
# Built here rather than borrowed from another suite: a real checkbox draws
# itself through an appearance stream, and the point is that Platen does not
# then offer a drawing on top of a box that already works.
real = os.path.join(sandbox.DIR, "realform.pdf")
rdoc = fitz.open()
rpage = rdoc.new_page(width=300, height=200)
from backend import forms as _forms  # noqa: E402
_forms.add_field(rdoc, 0, "checkbox", [40, 40, 54, 54], "agreed")
_forms.add_field(rdoc, 0, "text", [40, 80, 240, 100], "who")
rdoc.save(real)
rdoc.close()
doc = fitz.open(real)
check("the real fields are there", len(list(doc[0].widgets())), 2)
check("a page of real fields offers no drawn boxes",
      len(boxes.find(doc, 0)), 0)
doc.close()

doc = fitz.open(os.path.join(OUT, "fixture.pdf"))
check("an ordinary page offers none either", len(boxes.find(doc, 0)), 0)
doc.close()


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % FLAT.replace("\\", "/"))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(2.0)
    js("setTool('field')")
    time.sleep(2.5)

    print("")
    print("== from the window ==")
    check("the squares are there to click",
          js("document.querySelectorAll('.boxhit').length"), len(EXPECTED))
    check("none shows as ticked yet",
          js("document.querySelectorAll('.boxhit.ticked').length"), 0)
    check("the panel says where they came from",
          js("(document.getElementById('insp-body').textContent || '').toLowerCase()"),
          lambda t: "drawn square" in t or "no field behind" in t)

    js("document.querySelectorAll('.boxhit')[1].click()")
    time.sleep(3.0)
    check("clicking one ticks it",
          js("(S.boxes[1] || {}).ticked"), True)
    check("and it is shown as ticked",
          js("document.querySelectorAll('.boxhit.ticked').length"), 1)
    check("no dialog was involved",
          js("document.getElementById('modal-back').classList.contains('on')"), False)

    js("document.querySelectorAll('.boxhit')[1].click()")
    time.sleep(3.0)
    check("clicking it again clears it",
          js("(S.boxes[1] || {}).ticked"), False)
    check("and nothing is shown as ticked",
          js("document.querySelectorAll('.boxhit.ticked').length"), 0)

    print("")
    print("== and it can be undone like anything else drawn ==")
    js("document.querySelectorAll('.boxhit')[0].click()")
    time.sleep(3.0)
    check("ticked", js("(S.boxes[0] || {}).ticked"), True)
    js("ACTIONS.undo()")
    time.sleep(3.0)
    check("undo takes the tick back off",
          js("(S.boxes[0] || {}).ticked"), False)

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF tick boxes", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
