"""Tick boxes that are only drawings.

Plenty of forms are printed rather than built: the little square is a line
drawing, or the character U+2610, and there is no form field behind it. Every
PDF reader shows it and none of them let you tick it, so people print the page,
tick it with a pen and scan it back.

This finds those squares and lets them be ticked. A tick is a normal ink
annotation, so it undoes, flattens and prints like anything else drawn on a
page, and ticking the same box again takes it away.

Nothing here changes the document. Finding a square is just looking, and a
page where everything is clickable would be worse than one where nothing is -
so the tests that follow are deliberately mean, and a square has to look
genuinely like a tick box to count.
"""
from __future__ import annotations

import pymupdf as fitz

from .session import PdfError

# A tick box is small and square. Below 7pt it is a bullet or a dot; above
# 28pt it is a cell, a photo frame or a box for a signature.
MIN_SIDE = 7.0
MAX_SIDE = 28.0

# How far from square a tick box is allowed to be. Some are drawn a little
# wide, but a table cell is nowhere near.
MAX_ASPECT = 1.4

# A square filled in dark is a bullet point, not something to tick. A box
# filled pale is just a box with a background.
DARK_FILL = 0.55

# The characters forms use when the square is typed rather than drawn.
# U+2610 is the ballot box; the others get used for the same purpose.
BOX_GLYPHS = "☐□▢❑❏◻◽"
TICKED_GLYPHS = "☑☒✓✔"

# Written into the annotation so a tick can be found again and told apart
# from anything else someone has drawn on the page.
TICK_MARK = "Platen tick"


def _side_ok(rect) -> bool:
    w, h = rect.width, rect.height
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        return False
    short, long_ = min(w, h), max(w, h)
    return short > 0 and (long_ / short) <= MAX_ASPECT


def _is_dark(colour) -> bool:
    if not colour:
        return False
    try:
        return (sum(colour[:3]) / 3.0) < DARK_FILL
    except Exception:
        return False


def _overlaps(a, b, share: float = 0.5) -> bool:
    """Whether two rectangles are really the same square drawn twice."""
    hit = fitz.Rect(a) & fitz.Rect(b)
    if hit.is_empty:
        return False
    smaller = min(abs(fitz.Rect(a).get_area()), abs(fitz.Rect(b).get_area()))
    return smaller > 0 and abs(hit.get_area()) / smaller >= share


def _widget_rects(page) -> list:
    """Where the real form fields are, so a real one is never offered twice."""
    try:
        return [fitz.Rect(w.rect) for w in page.widgets()]
    except Exception:
        return []


def _drawn_squares(page) -> list:
    """Squares drawn as vector rectangles.

    PyMuPDF groups many rectangles into one path whose own rect is the bounding
    box of the lot, so the items are what matter here - reading path rects
    instead finds one enormous square and misses every real one.
    """
    found = []
    try:
        paths = page.get_drawings()
    except Exception:
        return found
    for path in paths:
        dark = _is_dark(path.get("fill"))
        for item in path.get("items", []):
            if not item or item[0] != "re":
                continue
            rect = fitz.Rect(item[1])
            if not _side_ok(rect):
                continue
            if dark:
                continue              # a filled-in square is a bullet
            found.append(rect)
    return found


def _glyph_squares(page) -> list:
    """Squares that are a character rather than a drawing."""
    found = []
    for glyph in BOX_GLYPHS:
        try:
            hits = page.search_for(glyph, quads=True)
        except Exception:
            continue
        for quad in hits:
            rect = quad.rect
            if not (MIN_SIDE <= max(rect.width, rect.height) <= MAX_SIDE):
                continue
            # A glyph's box runs from the ascender to the descender, so it is
            # taller than the square anyone can see. Squaring it off keeps the
            # tick inside the square instead of hanging below it.
            side = min(rect.width, rect.height)
            mid_x = (rect.x0 + rect.x1) / 2.0
            mid_y = (rect.y0 + rect.y1) / 2.0
            found.append(fitz.Rect(mid_x - side / 2, mid_y - side / 2,
                                   mid_x + side / 2, mid_y + side / 2))
    return found


def _ticks_on(page) -> list:
    """Our own ticks, as (annotation, rectangle)."""
    out = []
    try:
        annot = page.first_annot
    except Exception:
        return out
    while annot:
        try:
            info = annot.info or {}
            if info.get("title") == TICK_MARK or info.get("subject") == TICK_MARK:
                out.append((annot, fitz.Rect(annot.rect)))
        except Exception:
            pass
        annot = annot.next
    return out


def find(doc: fitz.Document, page_no: int) -> list[dict]:
    """Squares on this page that look like tick boxes and are not form fields."""
    page = doc[page_no]
    candidates = _drawn_squares(page) + _glyph_squares(page)

    skip = _widget_rects(page)
    ticked = _ticks_on(page)

    kept: list[fitz.Rect] = []
    for rect in candidates:
        # A real field is already tickable; offering a second one on top of it
        # would put a drawing over a working checkbox.
        if any(_overlaps(rect, w, 0.3) for w in skip):
            continue
        if any(_overlaps(rect, k) for k in kept):
            continue
        kept.append(rect)

    kept.sort(key=lambda r: (round(r.y0, 1), round(r.x0, 1)))
    return [{
        "page": page_no,
        "rect": [r.x0, r.y0, r.x1, r.y1],
        "ticked": any(_overlaps(r, t, 0.3) for _, t in ticked),
    } for r in kept]


def _tick_strokes(rect) -> list:
    """A tick that fits the square, drawn the way a person draws one."""
    r = fitz.Rect(rect)
    pad = max(1.0, min(r.width, r.height) * 0.16)
    left, right = r.x0 + pad, r.x1 - pad
    top, bottom = r.y0 + pad, r.y1 - pad
    # Down to the left foot, then up past the top right: two strokes of a pen.
    return [[(left, top + (bottom - top) * 0.55),
             (left + (right - left) * 0.36, bottom),
             (right, top)]]


def tick(doc: fitz.Document, page_no: int, rect: list[float],
         color: tuple = (0.05, 0.09, 0.36), width: float = 1.6) -> dict:
    """Draw a tick in this square."""
    page = doc[page_no]
    box = fitz.Rect(rect)
    if box.is_empty:
        raise PdfError("That is not a box.")
    annot = page.add_ink_annot(_tick_strokes(box))
    annot.set_colors(stroke=color)
    annot.set_border(width=max(0.8, min(width, box.width / 5.0)))
    # Marked so it can be found again, and so it is never confused with
    # something the person drew themselves.
    annot.set_info(title=TICK_MARK, subject=TICK_MARK)
    annot.update()
    return {"ticked": True, "id": annot.xref,
            "rect": [box.x0, box.y0, box.x1, box.y1]}


def untick(doc: fitz.Document, page_no: int, rect: list[float]) -> dict:
    """Take the tick out of this square. Only ever removes one of ours."""
    page = doc[page_no]
    box = fitz.Rect(rect)
    removed = 0
    for annot, where in _ticks_on(page):
        if _overlaps(box, where, 0.3):
            page.delete_annot(annot)
            removed += 1
    return {"ticked": False, "removed": removed,
            "rect": [box.x0, box.y0, box.x1, box.y1]}


def toggle(doc: fitz.Document, page_no: int, rect: list[float]) -> dict:
    """Tick an empty square, or clear one that is already ticked."""
    page = doc[page_no]
    box = fitz.Rect(rect)
    if any(_overlaps(box, where, 0.3) for _, where in _ticks_on(page)):
        return untick(doc, page_no, rect)
    return tick(doc, page_no, rect)
