"""Page numbers, headers and footers, and Bates numbers.

Checked the way a reader meets them: where on the page as it is shown, and
reading the right way up. Page numbers used to be placed by the page's shown
size in coordinates that ignore rotation, so on a page turned 90 degrees -
most scanned landscape pages - they landed off the page and were never seen.
"""
import datetime
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401  - keeps settings out of the real folder

import pymupdf as fitz

from backend import decorate
from backend.api import Api

fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-56s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def build(title=""):
    """Pages turned each way, then one cropped, each with a line of body text."""
    doc = fitz.open()
    for turn in (0, 90, 180, 270):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "Body text on a page turned %d" % turn, fontsize=12)
        page.set_rotation(turn)
    page = doc.new_page(width=612, height=792)
    page.insert_text((200, 300), "Body text on a cropped page", fontsize=12)
    page.set_cropbox(fitz.Rect(100, 100, 500, 600))
    if title:
        doc.set_metadata({"title": title})
    return doc


def seen(doc, needle):
    """Per page: (text, rect as shown, reads upright) for lines containing needle."""
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    pages = []
    for page in doc:
        turn = page.rotation_matrix
        spin = fitz.Matrix(turn.a, turn.b, turn.c, turn.d, 0, 0)
        found = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"])
                if needle in text:
                    way = fitz.Point(line["dir"]) * spin
                    found.append((text, fitz.Rect(line["bbox"]) * turn,
                                  abs(way.x - 1) < 0.01 and abs(way.y) < 0.01))
        pages.append((page.rect, found))
    return pages


def corner_ok(shown, rect, position, margin=36, slack=6):
    """Is `rect` in `position` of a page shown as `shown`, inside it?"""
    if not shown.contains(rect):
        return False
    horizontal = {"left": abs(rect.x0 - margin) < slack,
                  "right": abs(shown.x1 - margin - rect.x1) < slack,
                  "center": abs((rect.x0 + rect.x1) / 2 - shown.width / 2) < slack}
    vertical = {"top": rect.y0 < margin + 20, "bottom": rect.y1 > shown.y1 - margin - 6}
    up, across = position.split("-")
    return horizontal[across] and vertical[up]


print("== every position, on every way a page can be turned ==")
for position in decorate.CORNERS:
    doc = build()
    decorate.stamp(doc, "numbers", {position: "No. {page}"})
    results = seen(doc, "No. ")
    placed = [len(found) == 1 and corner_ok(shown, found[0][1], position)
              for shown, found in results]
    upright = [bool(found) and found[0][2] for _, found in results]
    check("%-13s in its corner on 0/90/180/270/cropped" % position, placed, [True] * 5)
    check("%-13s reads upright on all five" % position, upright, [True] * 5)

print("")
print("== numbering ==")
doc = build()
decorate.stamp(doc, "numbers", {"bottom-center": "Page {page} of {pages}"})
check("page n of total", [f[0][0] for _, f in seen(doc, "Page ")],
      ["Page %d of 5" % n for n in range(1, 6)])
decorate.stamp(doc, "numbers", {"bottom-center": "#{page}"}, start_at=10, skip_first=True)
check("starting at 10, first page left out",
      [f[0][0] if f else None for _, f in seen(doc, "#")],
      [None, "#11", "#12", "#13", "#14"])
check("and the earlier numbers were replaced, not doubled",
      [len(f) for _, f in seen(doc, "Page ")], [0] * 5)
doc = build()
decorate.stamp(doc, "numbers", {"top-right": "{page}"}, pages=[1, 2])
check("only the pages asked for", [len(f) for _, f in seen(doc, "Body")], [1] * 5)
check("  stamped 2 and 3", [f[0][0] if f else None for _, f in seen(doc, "2") + seen(doc, "3")
                            if f and len(f[0][0]) == 1], ["2", "3"])

print("")
print("== headers and footers ==")
doc = build(title="Purchase Agreement")
decorate.stamp(doc, "headfoot", {"top-left": "{title}", "top-right": "{date}",
                                 "bottom-left": "{file}", "bottom-right": "{page}/{pages}"},
               file_name="agreement.pdf")
today = datetime.date.today()
check("title placeholder", seen(doc, "Purchase")[0][1][0][0], "Purchase Agreement")
check("date placeholder", seen(doc, str(today.year))[0][1][0][0],
      "%s %d, %d" % (today.strftime("%B"), today.day, today.year))
check("file placeholder", seen(doc, "agreement.pdf")[3][1][0][0], "agreement.pdf")
check("four fields on every page", [len(f) for _, f in seen(doc, "")], lambda counts: all(c >= 5 for c in counts))

print("")
print("== kinds are kept apart ==")
doc = build()
decorate.stamp(doc, "numbers", {"bottom-center": "- {page} -"})
decorate.stamp(doc, "headfoot", {"top-center": "CONFIDENTIAL"})
decorate.stamp(doc, "bates", {"bottom-right": "{bates}"},
               bates={"prefix": "WCP", "start": 41, "digits": 6})
check("bates labels run on", [f[0][0] for _, f in seen(doc, "WCP")],
      ["WCP0000%d" % n for n in range(41, 46)])
removed = decorate.remove_stamps(doc, "numbers")
check("removing page numbers reports the pages", removed["pages"], 5)
check("  takes the numbers", [len(f) for _, f in seen(doc, "- ")], [0] * 5)
check("  leaves the header", [len(f) for _, f in seen(doc, "CONFIDENTIAL")], [1] * 5)
check("  leaves the Bates numbers", [len(f) for _, f in seen(doc, "WCP")], [1] * 5)
check("  and the page's own text", [len(f) for _, f in seen(doc, "Body text")], [1] * 5)
check("removing what is not there removes nothing",
      decorate.remove_stamps(doc, "numbers")["pages"], 0)

# A save that rewrites every content stream must not lose track of them.
again = fitz.open(stream=doc.tobytes(garbage=4, deflate=True, clean=True), filetype="pdf")
decorate.remove_stamps(again, "bates")
check("stamps still found after a cleaning save", [len(f) for _, f in seen(again, "WCP")], [0] * 5)
check("  the header still there", [len(f) for _, f in seen(again, "CONFIDENTIAL")], [1] * 5)

doc = build()
decorate.stamp(doc, "bates", {"bottom-right": "{bates}"},
               bates={"prefix": "A EMC ", "start": 1, "digits": 3})
decorate.remove_stamps(doc, "bates")
check("a prefix that looks like a PDF operator is still removed cleanly",
      ([len(f) for _, f in seen(doc, "EMC")], [len(f) for _, f in seen(doc, "Body")]),
      ([0] * 5, [1] * 5))

print("")
print("== Bates across open documents ==")
api = Api()
paths = []
for name, count in (("one", 3), ("two", 2)):
    path = os.path.join(sandbox.DIR, "bates-%s.pdf" % name)
    doc = fitz.open()
    for _ in range(count):
        doc.new_page().insert_text((72, 72), "Production %s" % name)
    doc.save(path)
    paths.append(path)
    api.open_path(path)
result = api.bates("WCP-", 100, 5, "", "bottom-right", 9, True)
check("the call succeeds", result["ok"], True)
data = result.get("data", {})
check("one sequence across both, in tab order",
      [(d["first"], d["last"]) for d in data.get("documents", [])],
      [("WCP-00100", "WCP-00102"), ("WCP-00103", "WCP-00104")])
check("and says where the next set starts", data.get("next"), 105)
check("both documents are marked changed",
      [t["dirty"] for t in api.tab_list()["data"]["tabs"]], [True, True])
api.undo()
check("undo takes it back from the document in front",
      [len(f) for _, f in seen(api._session.doc, "WCP")], [0, 0])

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
