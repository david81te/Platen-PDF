"""Editing text that does not run left to right across an upright page.

Three ways that happens: text turned on the page (a label up the margin, a
sideways table heading, text upside down), a whole page turned (most scanned
landscape pages), and text at an odd angle. These used to be refused outright.
Each must now follow the same rules as ordinary text - the edit stays on its
own row, nothing beside it is lost - measured along the way the text reads.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz

from backend import textedit

fails = []
ROWS = ["First row stays exactly here", "Second row is the one we edit", "Third row stays exactly here"]


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-60s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def turned_page(dx, dy, leading=1.05, size=11, rotation=0, at=(300, 400)):
    """Three rows of text running along (dx, dy), set close together."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    way = fitz.Point(dx, dy)
    down = fitz.Point(-dy, dx)
    draw = fitz.Matrix(dx, -dy, dy, dx, 0, 0)    # insert_text morphs y-up
    for i, text in enumerate(ROWS):
        where = fitz.Point(at) + down * (size * leading * i)
        page.insert_text(where, text, fontsize=size, morph=(where, draw))
    page.set_rotation(rotation)
    return fitz.open(stream=doc.tobytes(), filetype="pdf")


def lines(doc):
    page = fitz.open(stream=doc.tobytes(), filetype="pdf")[0]
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            out.append(("".join(s["text"] for s in line["spans"]),
                        tuple(round(v, 3) for v in line["dir"]),
                        fitz.Rect(line["bbox"]), line["spans"][0]["origin"]))
    return out


def span_reading(doc, needle):
    for block in textedit.layout(doc[0])["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                if needle in span["text"]:
                    return span
    raise AssertionError(needle)


def raw_span_id(doc, needle):
    """The id of the run reading `needle`, whatever the page's turn."""
    return span_reading(doc, needle)["id"]


def same(a, b, tol=0.6):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def edit_case(label, dx, dy, replacement, **build):
    doc = turned_page(dx, dy, **build)
    before = lines(doc)
    middle = next(l for l in before if l[0].startswith("Second"))
    try:
        result = textedit.edit_span(doc, raw_span_id(doc, "Second"), replacement)
    except ValueError as exc:
        check(label + ": edit accepted", str(exc), "accepted")
        return None
    after = lines(doc)
    kept = [l for l in before if not l[0].startswith("Second")]
    check(label + ": the other rows are untouched",
          [any(t == k[0] and d == k[1] and same(r, k[2]) for t, d, r, _ in after) for k in kept],
          [True, True])
    new = [l for l in after if replacement.split()[0] in l[0]]
    check(label + ": the new wording reads", " ".join(l[0] for l in new).strip(),
          lambda t: replacement.strip() in t)
    check(label + ": running the same way", [l[1] for l in new], lambda d: d and all(
        x == middle[1] for x in d))
    check(label + ": on the same baseline",
          [same((l[3][0] * -dy + l[3][1] * dx,), (middle[3][0] * -dy + middle[3][1] * dx,))
           for l in new], lambda s: s and all(s))
    check(label + ": still on the page", all(fitz.Rect(0, 0, 612, 792).contains(l[2]) for l in after),
          True)
    return result


print("== text turned on an upright page ==")
for name, (dx, dy) in {"reading upward": (0, -1), "reading downward": (0, 1),
                       "upside down": (-1, 0)}.items():
    edit_case(name + ", longer", dx, dy, "Second row now says rather more than it did")
    edit_case(name + ", shorter", dx, dy, "Second")

print("")
print("== room is measured the way the text runs ==")
# Upward text starting near the bottom of the page has only a few hundred
# points before the top; a long edit must shrink, not run off the page.
result = edit_case("long label up the margin", 0, -1,
                   "Second row " + "grows and grows " * 12, at=(40, 300))
check("  it shrank to fit", (result or {}).get("shrunk"), True)

print("")
print("== a turned page, as a scanned landscape page is ==")
for rotation in (90, 270):
    doc = turned_page(1, 0, rotation=rotation)
    span = span_reading(doc, "Second")
    shown = doc[0].rect
    check("rotation %d: the window gets places on the page as shown" % rotation,
          shown.contains(fitz.Rect(span["bbox"])), True)
    check("rotation %d: and the run is reported upright on screen" % rotation,
          span["upright"], False)
    before = lines(doc)
    textedit.edit_span(doc, span["id"], "Second row edited on a turned page")
    after = lines(doc)
    check("rotation %d: the edit lands" % rotation,
          any("edited on a turned page" in l[0] for l in after), True)
    check("rotation %d: the other rows are untouched" % rotation,
          [any(t == k[0] and same(r, k[2]) for t, _, r, _ in after)
           for k in before if not k[0].startswith("Second")], [True, True])

print("")
print("== a turned paragraph rewraps the way it reads ==")
# PyMuPDF makes each vertical line a block of its own, so the paragraph here
# is one line long, with free page to its side to wrap into.
doc = fitz.open()
page = doc.new_page(width=612, height=792)
draw = fitz.Matrix(0, 1, -1, 0, 0, 0)          # reading upward
where = fitz.Point(80, 700)
page.insert_text(where, "A short label up the side", fontsize=11, morph=(where, draw))
page.insert_text((300, 120), "Unrelated body text, upright.", fontsize=11)
doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
block = next(b for b in textedit.layout(doc[0])["blocks"] if "short label" in b["text"])
textedit.edit_block(doc, block["id"], "A rather longer label that now needs more than one "
                                      "line, so it wraps, and still runs up the side of "
                                      "the page the way it did before it was edited.")
after = lines(doc)
side = [l for l in after if l[1] == (0.0, -1.0)]
check("the paragraph still reads upward, over several lines", len(side), lambda n: n >= 2)
check("every word survived", " ".join(l[0] for l in side),
      lambda t: "longer label" in t and "before it was edited." in t)
check("each next line sits beside the last, as the text reads",
      [round(l[3][0]) for l in side], lambda xs: xs[0] >= 79 and all(b > a for a, b in zip(xs, xs[1:])))
check("starting where the label did", round(side[0][3][1]), lambda y: abs(y - 700) <= 2)
check("the upright text is untouched",
      any(t == "Unrelated body text, upright." for t, _, _, _ in after), True)

print("")
print("== text at an odd angle ==")
alone = turned_page(0.866, -0.5, leading=4.0)
try:
    textedit.edit_span(alone, raw_span_id(alone, "Second"), "Second row at an angle, edited")
    check("slanted text with room around it can be edited",
          any("at an angle, edited" in l[0] for l in lines(alone)), True)
    check("  and its neighbours are kept",
          sum(1 for l in lines(alone) if "stays exactly here" in l[0]), 2)
except ValueError as exc:
    check("slanted text with room around it can be edited", str(exc), "accepted")

# Solid leading at an angle: the redaction goes in short pieces, each with a
# small upright box, so even close rows are spared.
crowded = turned_page(0.866, -0.5, leading=1.0)
try:
    textedit.edit_span(crowded, raw_span_id(crowded, "Second"), "Second row, crowded")
    check("slanted text set solid can be edited", True, True)
    check("  and its neighbours are kept",
          sum(1 for l in lines(crowded) if "stays exactly here" in l[0]), 2)
except ValueError as exc:
    check("slanted text set solid can be edited", str(exc), "accepted")

print("")
print("== an edit that cannot spare its neighbours is refused ==")


def state(doc):
    # Saved bytes differ every time, so compare what the page holds.
    page = doc[0]
    return [doc.xref_stream(x) for x in page.get_contents()], page.get_text()


# Two lines printed over each other - the damage an old wrapping bug left.
# Their glyphs share a place, so neither can be taken without the other.
over = fitz.open()
page = over.new_page()
page.insert_text((72, 200), "Words printed here first", fontsize=11)
page.insert_text((72, 200), "Other words printed on top", fontsize=11)
over = fitz.open(stream=over.tobytes(), filetype="pdf")
before = state(over)
try:
    textedit.edit_span(over, raw_span_id(over, "Other words"), "Replacement")
    check("overprinted text is refused rather than half erased", "edited anyway", "refused")
except ValueError as exc:
    check("overprinted text is refused rather than half erased",
          "too close to other text" in str(exc), True)
    check("  and the page is left exactly as it was", state(over) == before, True)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
