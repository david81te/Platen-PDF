"""Everything the window places on a page lands where it was drawn, on a turned page too.

The window draws a page as it is shown; PyMuPDF places and reports things in
the page's unrotated coordinates. On a page turned 90 degrees - most scanned
landscape pages - every highlight, shape, note, field, link and signature
landed somewhere other than where it was drawn, search hits were highlighted
somewhere else, and text boxes, stamps, fields and signatures read sideways.

Each check here draws something through the API the way the window does, in
the coordinates of the page as shown, and then looks at the rendered page.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401  - keeps the signature library out of the real folder

import numpy as np
import pymupdf as fitz

from backend import signatures
from backend.api import Api

fails = []
TARGET = [300, 150, 460, 210]            # where the window asks, as shown


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-62s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def pixels(page):
    pix = page.get_pixmap(alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def changed(before, after):
    diff = np.abs(after.astype(int) - before.astype(int)).sum(axis=2) > 30
    ys, xs = np.nonzero(diff)
    return None if not len(xs) else fitz.Rect(int(xs.min()), int(ys.min()),
                                               int(xs.max()) + 1, int(ys.max()) + 1)


def near(found, wanted, slack=14):
    return found is not None and fitz.Rect(wanted).intersects(found) and \
        abs(found.x0 - wanted[0]) <= slack and abs(found.y0 - wanted[1]) <= slack


opened = []


def open_turned(rotation):
    # A file of its own each time: the last case's document still holds its.
    opened.append(rotation)
    path = os.path.join(sandbox.DIR, "turned-%d-%d.pdf" % (rotation, len(opened)))
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.set_rotation(rotation)
    page.insert_text(fitz.Point(310, 185) * page.derotation_matrix, "findme here",
                     fontsize=14, rotate=rotation)
    doc.save(path)
    doc.close()
    api = Api()
    api.open_path(path)
    return api


def reads_upright(doc, needle):
    """Shown reading direction of `needle` once annotations are drawn into the page."""
    flat = fitz.open(stream=doc.tobytes(), filetype="pdf")
    flat.bake()
    page = flat[0]
    m = page.rotation_matrix
    spin = fitz.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if needle in "".join(s["text"] for s in line["spans"]):
                way = fitz.Point(line["dir"]) * spin
                return abs(way.x - 1) < 0.05 and abs(way.y) < 0.05
    return None


PLACING = [
    ("highlight", lambda a: a.annot_markup(0, "highlight", [TARGET], snap=False)),
    ("rectangle", lambda a: a.annot_shape(0, "rect", [TARGET[:2], TARGET[2:]], [0, 0, 1])),
    ("ellipse", lambda a: a.annot_shape(0, "circle", [TARGET[:2], TARGET[2:]], [0, 0, 1])),
    ("line", lambda a: a.annot_shape(0, "line", [TARGET[:2], TARGET[2:]], [0, 0, 1])),
    ("ink", lambda a: a.annot_ink(0, [[TARGET[:2], TARGET[2:]]], [0, 0, 1], 3)),
    ("note", lambda a: a.annot_note(0, TARGET[:2], "a note")),
    ("text box", lambda a: a.annot_textbox(0, TARGET, "Boxed words", 12, [0, 0, 0], None, None)),
    ("stamp", lambda a: a.annot_stamp(0, TARGET, "approved")),
    ("redaction mark", lambda a: a.redact_mark(0, [TARGET])),
    ("added text", lambda a: a.add_text(0, TARGET, "Added words")),
    ("form field", lambda a: a.form_add(0, "text", TARGET, "field", "Field words")),
]

for rotation in (0, 90, 180, 270):
    print("== a page turned %d degrees ==" % rotation)
    for label, call in PLACING:
        api = open_turned(rotation)
        before = pixels(api._session.doc[0])
        result = call(api)
        found = changed(before, pixels(api._session.doc[0]))
        check("%-15s lands where it was drawn" % label,
              (result.get("ok"), near(found, TARGET)), (True, True))

    # Text in things that carry it must read across the page as shown.
    for label, call, needle in (("text box", PLACING[6][1], "Boxed"),
                                ("added text", PLACING[9][1], "Added"),
                                ("form field", PLACING[10][1], "Field")):
        api = open_turned(rotation)
        call(api)
        check("%-15s reads upright" % label, reads_upright(api._session.doc, needle), True)

    api = open_turned(rotation)
    hit = api.search("findme")["data"]["hits"][0]["rect"]
    check("search highlights the words as shown", near(fitz.Rect(hit), [310, 170, 400, 190], 12),
          True)
    api.annot_shape(0, "rect", [TARGET[:2], TARGET[2:]], [0, 0, 1])
    listed = api.annot_list(0)["data"][0]["rect"]
    check("an annotation is listed where it shows", near(fitz.Rect(listed), TARGET, 3), True)
    # Moving it in the window's coordinates puts it where it was moved to.
    moved = [200, 300, 360, 360]
    before = pixels(api._session.doc[0])
    api.annot_update(0, api.annot_list(0)["data"][0]["id"], moved)
    check("and moves to where it was dragged",
          near(fitz.Rect(api.annot_list(0)["data"][0]["rect"]), moved, 3), True)
    api.form_add(0, "text", TARGET, "listed", "x")
    field = next(f for f in api.form_list(0)["data"] if f["name"] == "listed")
    check("a form field is listed where it shows", near(fitz.Rect(field["rect"]), TARGET, 3), True)
    api.link_add(0, TARGET, "https://example.com")
    link = api.link_list(0)["data"][0]["rect"]
    check("a link is listed where it was drawn", near(fitz.Rect(link), TARGET, 3), True)

print("== signatures ==")
picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
picture.set_rect(picture.irect, (255, 255, 255))
picture.set_rect(fitz.IRect(0, 0, 100, 100), (255, 0, 0))     # red on the left
png = os.path.join(sandbox.DIR, "half-red.png")
picture.save(png)
identifier = signatures.add(png, "Turned page test", drop_background=False)["id"]


def red_box(page):
    a = pixels(page)
    ys, xs = np.nonzero((a[:, :, 0] > 200) & (a[:, :, 1] < 80))
    return fitz.Rect(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


for rotation in (0, 90, 180, 270):
    for flatten in (False, True):
        api = open_turned(rotation)
        api.sig_place(0, identifier, [200, 200, 400, 300], "", flatten)
        red = red_box(api._session.doc[0])
        # Upright means the red half is on the left of the box as shown.
        upright = abs(red.x0 - 200) < 3 and abs(red.x1 - 300) < 4 and abs(red.y0 - 200) < 3
        check("rotation %3d: %s signature upright where drawn"
              % (rotation, "flattened" if flatten else "movable"), upright, True)
    api = open_turned(rotation)
    placed = api.sig_place(0, identifier, [200, 200, 400, 300])["data"]
    api.annot_update(0, placed["id"], [250, 400, 450, 500])
    red = red_box(api._session.doc[0])
    check("rotation %3d: and still upright after it is moved" % rotation,
          abs(red.x0 - 250) < 3 and abs(red.x1 - 350) < 4 and abs(red.y0 - 400) < 3, True)

print("== comparing turned pages ==")
older = fitz.open()
page = older.new_page()
page.set_rotation(90)
page.insert_text(fitz.Point(100, 100) * page.derotation_matrix, "The price is ten", fontsize=14, rotate=90)
newer = fitz.open()
page = newer.new_page()
page.set_rotation(90)
page.insert_text(fitz.Point(100, 100) * page.derotation_matrix, "The price is twelve", fontsize=14, rotate=90)
from backend import compare  # noqa: E402
result = compare.compare(older, newer, visual=False)
added = result["pages"][0]["added_rects"]
check("an added word is boxed where it shows", added and near(fitz.Rect(added[0]), [180, 85, 230, 105], 25),
      True)

print("== what a review of turned pages found ==")


def saved(doc, name):
    path = os.path.join(sandbox.DIR, "%s-%d.pdf" % (name, len(opened)))
    opened.append(name)
    doc.save(path)
    doc.close()
    api = Api()
    api.open_path(path)
    return api


# A scan of words that reads upright on a page turned 90 degrees.
scan = fitz.open()
sheet = scan.new_page(width=792, height=612)
sheet.insert_text((100, 200), "INVOICE NUMBER 12345", fontsize=36)
picture = sheet.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).tobytes("png")
for rotation in (90, 270):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.set_rotation(rotation)
    page.insert_image(page.rect * page.derotation_matrix, stream=picture, rotate=rotation)
    api = saved(doc, "scan")
    api.ocr_run("eng", 150, False)
    hits = api.search("INVOICE")["data"]["hits"]
    check("rotation %3d: recognised words are found where they show" % rotation,
          bool(hits) and near(fitz.Rect(hits[0]["rect"]), [100, 165, 250, 210], 16), True)

for rotation in (90, 180, 270):
    api = open_turned(rotation)
    api.page_crop(0, [100, 100, 500, 400])
    check("rotation %3d: a crop is the size it was drawn" % rotation,
          [round(v) for v in api._page(0).rect], [0, 0, 400, 300])
    api.page_crop(0, [0, 0, 300, 200])          # again, inside the first
    hit = api.search("findme")["data"]["hits"]
    check("rotation %3d: and cropping again keeps what was inside" % rotation,
          bool(hit) and near(fitz.Rect(hit[0]["rect"]), [210, 70, 252, 89], 3), True)

for rotation in (90, 180, 270):
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.xref_set_key(doc[0].xref, "CropBox", "[50 92 560 712]")
    doc.xref_set_key(doc[0].xref, "Rotate", str(rotation))
    api = saved(fitz.open(stream=doc.tobytes(), filetype="pdf"), "cropped")
    api.link_add(0, [150, 100, 260, 150], "https://example.com")
    link = api.link_list(0)["data"][0]["rect"]
    check("rotation %3d: a link on a cropped page lands where drawn" % rotation,
          near(fitz.Rect(link), [150, 100, 260, 150], 1), True)

    api = open_turned(rotation)
    api.boxes_toggle(0, [100, 100, 120, 120])
    page = api._session.doc[0]
    stroke = page.first_annot.vertices
    stroke = stroke[0] if stroke and isinstance(stroke[0][0], (list, tuple)) else stroke
    shown = [fitz.Point(p) * page.rotation_matrix for p in stroke]
    check("rotation %3d: a tick is the right way up" % rotation,
          [(round(p.x), round(p.y)) for p in shown], [(103, 111), (108, 117), (117, 103)])

    api = open_turned(rotation)
    api.annot_stamp(0, TARGET, "draft")
    stamp = api._session.doc[0].first_annot
    check("rotation %3d: a stamp is still a Draft stamp" % rotation,
          (stamp.info.get("content"), api._session.doc.xref_get_key(stamp.xref, "Name")[1]),
          ("Draft", "/Draft"))

    doc = fitz.open()
    page = doc.new_page()
    page.set_rotation(rotation)
    for row, words in enumerate(("first line alpha words", "second line bravo words",
                                 "third line charlie words")):
        page.insert_text(fitz.Point(72, 100 + 20 * row) * page.derotation_matrix, words,
                         fontsize=12, rotate=rotation)
    api = saved(doc, "lines")
    check("rotation %3d: a search hit quotes its own line" % rotation,
          api.search("charlie")["data"]["hits"][0]["snippet"], "third line charlie words")

    older, newer = fitz.open(), fitz.open()
    for doc, line in ((older, "the quick fox jumps"), (newer, "the quick red fox jumps")):
        page = doc.new_page()
        page.set_rotation(rotation)
        for row, words in enumerate((line, "over the lazy dog", "and runs far away")):
            page.insert_text(fitz.Point(72, 100 + 20 * row) * page.derotation_matrix, words,
                             fontsize=12, rotate=rotation)
    found = compare.compare(older, newer, visual=False)["pages"][0]
    check("rotation %3d: comparing finds the one word added" % rotation,
          (found["added_words"], found["removed_words"]), (1, 0))

print("== comparing when this one is the earlier version ==")


def written(name, text):
    path = os.path.join(sandbox.DIR, name)
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), text, fontsize=14)
    doc.save(path)
    doc.close()
    return path


api = Api()
api.open_path(written("newer.pdf", "The price is twelve dollars"))
api.open_path(written("older.pdf", "The price is ten dollars"))       # in front
result = api.compare_tab(0, False, "current")["data"]
check("the comparison knows the front one is the earlier", result["front"], "left")
api.compare_markup()
marks = [(a.type[1], a.info.get("content", "")[:7]) for a in api._session.doc[0].annots()]
check("marking up marks what was taken out of it, not what the other added",
      marks, [("Highlight", "Removed"), ("Text", "1 word ")])
api.tab_switch(0)
check("and marking up another document with it is refused", api.compare_markup()["ok"], False)

print("\nRESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
