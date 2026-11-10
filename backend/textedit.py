"""In-place text editing for PDFs.

A PDF stores positioned glyph draws, not editable paragraphs, so "editing text"
means removing the original glyphs and re-typesetting replacements. Two units:

  span  - rewrite one run of same-styled text. The runs touching it on its line
          are redrawn with it so they move along instead of being overprinted;
          nothing on any other row is touched.
  block - rewrite a paragraph, re-wrapping across lines inside its bounding box.

Tuned for PDFs exported from Word, which produce clean, well-separated text runs.
"""
from __future__ import annotations

import gc
import html as html_mod
import math
import re
import unicodedata

import pymupdf as fitz

from . import fonts as fontmod

# Keep line art and images when removing text; a redaction that wipes graphics
# would erase table borders and shading around the edited text.
_REDACT_KW = {}
for _name, _attr in (("images", "PDF_REDACT_IMAGE_NONE"),
                     ("graphics", "PDF_REDACT_LINE_ART_NONE"),
                     ("text", "PDF_REDACT_TEXT_REMOVE")):
    _value = getattr(fitz, _attr, None)
    if _value is not None:
        _REDACT_KW[_name] = _value


class EditRefused(ValueError):
    """An edit turned down before anything on the page was changed."""


def _text_dict(page: fitz.Page) -> dict:
    """The page's own text, as get_text("dict") gives it, without annotations.

    get_text reads comments and form fields too, so a text box or a field's
    value was offered for editing as if it were page text - and editing it
    deleted the comment and burned the words into the page beneath.
    """
    turn = page.rotation
    if turn:
        page.set_rotation(0)     # as get_textpage does: unturned coordinates
    try:
        textpage = fitz.TextPage(page.get_displaylist(annots=False)
                                 .get_textpage(fitz.TEXTFLAGS_DICT))
        textpage.parent = page
        return textpage.extractDICT()
    finally:
        if turn:
            page.set_rotation(turn)


def _apply_redactions(doc: fitz.Document, number: int, areas) -> None:
    """Remove the text under `areas`, and touch nothing else on the page.

    Applying redactions applies every redaction mark on the page, including
    ones the person has not applied yet, and MuPDF also deletes comments and
    links it overlaps. So the page's annotations are set aside while it runs
    and put back after.

    MuPDF keeps its own list of a loaded page's annotations, so the page is
    reloaded each time - which only works if nothing else holds the page.
    Callers let go of theirs first; this checks before changing anything.
    """
    page = doc[number]
    kind, saved = doc.xref_get_key(page.xref, "Annots")
    if kind != "null":
        if page.this.m_internal.refs != 1:
            gc.collect()                # a page held only by a reference cycle
        if page.this.m_internal.refs != 1:
            raise RuntimeError("The page is still in use elsewhere; nothing was changed.")
        doc.xref_set_key(page.xref, "Annots", "null")
        page = doc.reload_page(page)
    for area in areas:
        page.add_redact_annot(area)
    page.apply_redactions(**_REDACT_KW)
    if kind != "null":
        doc.xref_set_key(page.xref, "Annots", saved)
        doc.reload_page(page)


def _right_to_left(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in ("R", "AL", "RLE", "RLO", "RLI")
               for ch in text)


def _unreadable_fonts(page: fitz.Page) -> set[str]:
    """Fonts on the page whose text cannot be trusted to be what is drawn.

    A composite or Type 3 font with no ToUnicode map does not say which
    characters its glyphs are, and what extraction reports is a guess -
    often the glyph numbers. Redrawing a run from that text draws the guess.
    """
    out = set()
    doc = page.parent
    try:
        listed = page.get_fonts(full=False)
    except Exception:
        return out
    for xref, _, kind, basefont, *_ in listed:
        if kind not in ("Type0", "Type3"):
            continue
        try:
            missing = doc.xref_get_key(xref, "ToUnicode")[0] == "null"
        except Exception:
            missing = True
        if missing:
            out.add(_font_key(basefont))
    return out


def _font_key(name: str) -> str:
    """A font's name as both its dictionary and its text agree on it.

    The font dictionary may say "Georgia Regular" where the text says
    "Georgia", so spacing, hyphens and a trailing "regular" are left out.
    """
    key = re.sub(r"[\s_-]+", "", fontmod.normalize(name).lower())
    return key[:-7] if key.endswith("regular") and len(key) > 7 else key


def _readable(span: dict, unreadable: set[str]) -> bool:
    return "\ufffd" not in span.get("text", "") and \
        _font_key(span.get("font", "")) not in unreadable


def _color_tuple(value: int | None) -> tuple[float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0)
    try:
        return fitz.sRGB_to_pdf(value)
    except Exception:
        return (0.0, 0.0, 0.0)


def _flags(span: dict) -> tuple[bool, bool]:
    flags = span.get("flags", 0)
    return bool(flags & 16), bool(flags & 2)  # bold, italic


class FontBinder:
    """Registers fonts on a page and hands back aliases + metric objects."""

    def __init__(self, doc: fitz.Document):
        self.resolver = fontmod.FontResolver(doc)
        self._bound: dict[tuple[int, str], tuple[str, fitz.Font]] = {}
        self._counter = 0

    def bind(self, page: fitz.Page, span_font: str, text: str = "") -> tuple[str, fitz.Font]:
        """Return (page font alias, Font) able to render `text`."""
        key = (page.number, span_font)
        cached = self._bound.get(key)
        # Still on the page? Applying a redaction takes fonts nothing uses
        # any more off the page, and drawing with an alias that has gone
        # fails - after the old words were already removed.
        if cached and self._covers(cached[1], text, cached[2]) and \
                cached[0] in {item[4] for item in page.get_fonts(full=False)}:
            return cached[0], cached[1]

        spec = self.resolver.resolve(page, span_font)
        candidates = [spec]
        if spec["kind"] != "base14":
            candidates.append({
                "kind": "base14",
                "base14": fontmod.base14_for(span_font, spec["bold"], spec["italic"]),
            })
        # Last, fonts that reach past Latin: a base-14 font cannot draw an en
        # dash, a euro sign or a Cyrillic letter, and drew a dot for each.
        candidates += [{"kind": "file", "path": path} for path in fontmod.unicode_fonts()]

        for candidate in candidates:
            try:
                alias, font = self._register(page, candidate)
            except Exception:
                continue
            latin = candidate["kind"] == "base14"
            if self._covers(font, text, latin):
                self._bound[key] = (alias, font, latin)
                return alias, font

        alias, font = self._register(page, {"kind": "base14", "base14": "helv"})
        self._bound[key] = (alias, font, True)
        return alias, font

    def _register(self, page: fitz.Page, spec: dict) -> tuple[str, fitz.Font]:
        if spec["kind"] == "base14":
            name = spec["base14"]
            page.insert_font(fontname=name)
            return name, fitz.Font(fontname=name)

        self._counter += 1
        alias = f"ZE{self._counter}"
        if spec["kind"] == "file":
            page.insert_font(fontname=alias, fontfile=spec["path"])
            return alias, fitz.Font(fontfile=spec["path"])

        page.insert_font(fontname=alias, fontbuffer=spec["buffer"])
        return alias, fitz.Font(fontbuffer=spec["buffer"])

    @staticmethod
    def _covers(font: fitz.Font, text: str, latin: bool = False) -> bool:
        """True if the font can draw every non-space character.

        A base-14 font (`latin`) says it has glyphs it cannot be asked for:
        text is written to it in a one-byte encoding.
        """
        for ch in text:
            if ch in " \t\r\n":
                continue
            if latin and ord(ch) > 0xFF:
                return False
            try:
                if not font.has_glyph(ord(ch)):
                    return False
            except Exception:
                return False
        return True


def layout(page: fitz.Page) -> dict:
    """Editable text structure for one page: blocks -> lines -> spans.

    In the coordinates of the page as it is shown, which is what the window
    draws on. PyMuPDF reports text in the page's unrotated coordinates, so on
    a page turned 90 degrees every place to click landed somewhere else.
    """
    raw = _text_dict(page)
    turn = page.rotation_matrix
    spin = fitz.Matrix(turn.a, turn.b, turn.c, turn.d, 0, 0)

    def shown(box) -> list:
        return list(fitz.Rect(box) * turn)

    blocks = []
    for b_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        lines = []
        for l_index, line in enumerate(block.get("lines", [])):
            way = fitz.Point(line.get("dir", (1, 0))) * spin
            upright = abs(way.x - 1) < 0.01 and abs(way.y) < 0.01
            spans = []
            for s_index, span in enumerate(line.get("spans", [])):
                if not span.get("text"):
                    continue
                bold, italic = _flags(span)
                origin = span.get("origin", (span["bbox"][0], span["bbox"][3]))
                spans.append({
                    "id": f"{page.number}:{b_index}:{l_index}:{s_index}",
                    "text": span["text"],
                    "bbox": shown(span["bbox"]),
                    "origin": list(fitz.Point(origin) * turn),
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 11.0), 2),
                    "color": span.get("color", 0),
                    "bold": bold,
                    "italic": italic,
                    "upright": upright,
                })
            if spans:
                lines.append({
                    "id": f"{page.number}:{b_index}:{l_index}",
                    "bbox": shown(line["bbox"]),
                    "dir": [round(way.x, 4), round(way.y, 4)],
                    "upright": upright,
                    "spans": spans,
                })
        if lines:
            blocks.append({
                "id": f"{page.number}:{b_index}",
                "bbox": shown(block["bbox"]),
                "lines": lines,
                "upright": all(ln["upright"] for ln in lines),
                "text": "\n".join("".join(s["text"] for s in ln["spans"]) for ln in lines),
            })
    return {"page": page.number, "blocks": blocks}


class _Frame:
    """A line's own coordinates: x along the text, y down from its baseline.

    Every rule the editor follows - which runs flow together, how much room
    a row has, the thin band to redact, where a paragraph's next line goes -
    is written for text running left to right. In this frame all text does,
    so text turned 90 or 180 degrees, or running up a page's margin, is edited
    by the same rules. For ordinary text the frame is the page itself.
    """

    def __init__(self, direction=(1.0, 0.0)):
        dx, dy = direction
        length = math.hypot(dx, dy) or 1.0
        dx, dy = dx / length, dy / length
        self.upright = abs(dx - 1) < 1e-3 and abs(dy) < 1e-3
        # A quarter, half or three-quarter turn keeps boxes exact.
        self.square = min(abs(dx), abs(dy)) < 1e-3
        self.direction = (dx, dy)
        self.to_page = fitz.Matrix(dx, dy, -dy, dx, 0, 0)
        self.to_local = fitz.Matrix(dx, -dy, dy, dx, 0, 0)

    def rect(self, box) -> fitz.Rect:
        return fitz.Rect(box) if self.upright else fitz.Rect(box) * self.to_local

    def point(self, at) -> fitz.Point:
        return fitz.Point(at) if self.upright else fitz.Point(at) * self.to_local

    def area(self, box: fitz.Rect):
        """A rectangle in this frame, as something a redaction can cover."""
        return fitz.Rect(box) if self.upright else fitz.Rect(box).quad * self.to_page

    def put(self, page: fitz.Page, at, text: str, **options) -> None:
        if self.upright:
            page.insert_text(fitz.Point(at), text, **options)
            return
        where = fitz.Point(at) * self.to_page
        # insert_text applies its morph with y pointing up, so the turn it
        # needs is the mirror of the one that maps this frame onto the page.
        page.insert_text(where, text, morph=(where, self.to_local), **options)


_UPRIGHT = _Frame()


def _local_span(span: dict, direction, frame: _Frame) -> dict:
    """A copy of a run with its box and origin in the line's own frame."""
    if frame.square:
        box = frame.rect(span["bbox"])
    else:
        box = (fitz.recover_span_quad(direction, span) * frame.to_local).rect
    origin = span.get("origin", (span["bbox"][0], span["bbox"][3]))
    return {**span, "bbox": list(box), "origin": list(frame.point(origin))}


def _local_line(line: dict, frame: _Frame) -> dict:
    direction = line.get("dir", (1, 0))
    spans = [_local_span(span, direction, frame) for span in line.get("spans", [])]
    box = fitz.Rect()
    for span in spans:
        box |= fitz.Rect(span["bbox"])
    if box.is_empty:
        box = frame.rect(line["bbox"])
    return {**line, "bbox": list(box), "spans": spans}


def _local_block(block: dict, frame: _Frame) -> dict:
    lines = [_local_line(line, frame) for line in block.get("lines", [])]
    box = fitz.Rect()
    for line in lines:
        box |= fitz.Rect(line["bbox"])
    return {**block, "bbox": list(box), "lines": lines}


def _shown_area(page: fitz.Page) -> fitz.Rect:
    """The visible page in the unrotated coordinates the text is reported in."""
    return page.rect * page.derotation_matrix


def _locate(page: fitz.Page, target_block: int, target_line: int | None = None):
    raw = _text_dict(page)
    blocks = raw.get("blocks", [])
    if target_block >= len(blocks):
        raise ValueError("Text block no longer exists on this page.")
    block = blocks[target_block]
    if block.get("type") != 0:
        raise ValueError("That region is not editable text.")
    if target_line is None:
        return block, None
    lines = block.get("lines", [])
    if target_line >= len(lines):
        raise ValueError("Text line no longer exists on this page.")
    return block, lines[target_line]


def parse_id(span_id: str) -> list[int]:
    try:
        return [int(part) for part in str(span_id).split(":")]
    except ValueError:
        raise ValueError("That text reference is not valid.") from None


def _page(doc: fitz.Document, page_no: int) -> fitz.Page:
    if page_no < 0 or page_no >= doc.page_count:
        raise ValueError("That text is on a page that no longer exists.")
    return doc[page_no]


def edit_span(doc: fitz.Document, span_id: str, new_text: str,
              binder: FontBinder | None = None) -> dict:
    """Replace one text run without touching any other row of the page.

    The run is redrawn together with the runs touching it on its line, so a
    longer word pushes the rest of its sentence along. Text further along the
    line -- a table cell, a tab stop -- stays where it is. Wording wider than
    the row has room for is drawn smaller until it fits. It used to wrap, which
    laid the tail over the row beneath, and re-wrapping the paragraph instead
    merged separate rows into one.

    Works in the line's own frame, so the same holds for text that is turned
    or runs vertically.
    """
    parts = parse_id(span_id)
    if len(parts) != 4:
        raise ValueError("That text reference is not valid.")
    page_no, b_index, l_index, s_index = parts
    page = _page(doc, page_no)
    _, line = _locate(page, b_index, l_index)
    if s_index >= len(line["spans"]):
        raise ValueError("Text run no longer exists on this page.")

    frame = _Frame(line.get("dir", (1, 0)))
    line = _local_line(line, frame)
    target = line["spans"][s_index]
    spans = [s for s in line["spans"] if s.get("text") or s is target]
    group, next_x = _touching_runs(spans, target)
    new_text = re.sub(r"[\r\n\t]+", " ", new_text)   # one row holds one line

    unreadable = _unreadable_fonts(page)
    if not _readable(target, unreadable):
        raise EditRefused("This text's font does not record which letters it draws, so "
                          "it can't be changed without garbling it. Nothing was changed.")
    # Neighbours are redrawn from their text; one whose text is a guess would
    # be redrawn as the guess. It stays where it is, as a fixed edge.
    at = next(i for i, span in enumerate(group) if span is target)
    first, last = at, at
    while first > 0 and _readable(group[first - 1], unreadable):
        first -= 1
    while last < len(group) - 1 and _readable(group[last + 1], unreadable):
        last += 1
    if last < len(group) - 1:
        next_x = group[last + 1]["bbox"][0]
    group = group[first:last + 1]
    if _right_to_left(new_text) or any(_right_to_left(s.get("text", "")) for s in group):
        raise EditRefused("Right-to-left text can't be edited here yet, so nothing was "
                          "changed.")

    runs = [{
        "text": new_text if span is target else span["text"],
        "font": span.get("font", ""),
        "size": span.get("size", 11.0),
        "color": span.get("color", 0),
        "baseline": span.get("origin", (0, span["bbox"][3]))[1],
        # Recognised text over a scan is invisible; it must stay so.
        "invisible": span.get("alpha", 255) == 0,
    } for span in group]

    start_x = group[0]["bbox"][0]
    original_end = max(span["bbox"][2] for span in group)
    anchor = None
    if next_x is None and _right_aligned(page, frame, start_x, original_end, b_index, l_index):
        # A figure in a right-aligned column grows to the left, as typed.
        anchor = original_end
        available = original_end - _room_left(page, group, spans, b_index, l_index, frame)
    else:
        room = _room_right(page, group, next_x, b_index, l_index, frame)
        available = max(original_end, room) - start_x

    page = None          # let go: the redaction reloads the page
    _redact_spans(doc, page_no, group, frame)
    page = doc[page_no]
    return _draw_runs(page, runs, start_x, binder or FontBinder(doc), available, frame,
                      anchor)


def _touching_runs(spans: list[dict], target: dict) -> tuple[list[dict], float | None]:
    """The runs that flow together with `target`, and where the next piece starts.

    A gap wider than a space or two between runs on one line marks a separate
    piece: a table cell, a tab stop, a second column. Those keep their place.
    """
    groups = [[spans[0]]]
    for prev, span in zip(spans, spans[1:]):
        gap = span["bbox"][0] - prev["bbox"][2]
        if gap > 0.6 * max(prev.get("size", 11.0), span.get("size", 11.0)):
            groups.append([])
        groups[-1].append(span)
    for index, group in enumerate(groups):
        if any(span is target for span in group):
            following = groups[index + 1][0]["bbox"][0] if index + 1 < len(groups) else None
            return group, following
    return [target], None


def _right_aligned(page: fitz.Page, frame: "_Frame", start: float, end: float,
                   b_index: int, l_index: int) -> bool:
    """Whether a row's last piece lines up on the right with another row.

    Its right edge meets another line's, and their left edges differ - which
    a left-aligned or justified paragraph's lines do not.
    """
    dx, dy = frame.direction
    for bi, block in enumerate(_text_dict(page).get("blocks", [])):
        if block.get("type") != 0:
            continue
        for li, other in enumerate(block.get("lines", [])):
            if (bi, li) == (b_index, l_index):
                continue
            if abs(other["dir"][0] - dx) > 1e-3 or abs(other["dir"][1] - dy) > 1e-3:
                continue
            box = frame.rect(other["bbox"])
            if abs(box.x1 - end) < 1.0 and abs(box.x0 - start) > 1.0:
                return True
    return False


def _room_left(page: fitz.Page, group: list[dict], spans: list[dict],
               b_index: int, l_index: int, frame: "_Frame") -> float:
    """How far left right-aligned text may grow before meeting anything."""
    size = max(span.get("size", 11.0) for span in group)
    baseline = group[0].get("origin", (0, group[0]["bbox"][3]))[1]
    start = group[0]["bbox"][0]
    end = max(span["bbox"][2] for span in group)
    row_top, row_bottom = baseline - 0.7 * size, baseline - 0.1 * size

    def on_row(box: fitz.Rect) -> bool:
        return box.y0 < row_bottom and box.y1 > row_top

    area = frame.rect(_shown_area(page))
    limit = area.x0 + 18.0
    for span in spans:
        if not any(span is mine for mine in group) and span["bbox"][2] <= start + 0.5:
            limit = max(limit, span["bbox"][2] + 0.3 * size)
    for bi, block in enumerate(_text_dict(page).get("blocks", [])):
        if block.get("type") != 0:
            continue
        for li, other in enumerate(block.get("lines", [])):
            box = frame.rect(other["bbox"])
            if (bi, li) != (b_index, l_index) and on_row(box) and box.x1 <= start + 1:
                limit = max(limit, box.x1 + 0.3 * size)
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        box = frame.rect(drawing["rect"])
        if not on_row(box):
            continue
        if box.x1 <= start + 0.5:                   # a rule or border to the left
            limit = max(limit, box.x1 + 1.5)
        elif box.x1 >= end - 0.5 and area.x0 + 1 < box.x0 <= start:
            limit = max(limit, box.x0 + 1.5)        # a cell drawn around it
    return limit


def _room_right(page: fitz.Page, group: list[dict], next_x: float | None,
                b_index: int, l_index: int, frame: _Frame = _UPRIGHT) -> float:
    """How far along its row the edited text may reach before meeting anything.

    In the line's frame: `group` is already there, and the page's other
    content is brought into it. Must be called before the redaction, while
    the neighbours are still there.
    """
    size = max(span.get("size", 11.0) for span in group)
    baseline = group[0].get("origin", (0, group[0]["bbox"][3]))[1]
    x0 = group[0]["bbox"][0]
    end = max(span["bbox"][2] for span in group)
    row_top, row_bottom = baseline - 0.7 * size, baseline - 0.1 * size

    def on_row(box: fitz.Rect) -> bool:
        return box.y0 < row_bottom and box.y1 > row_top

    area = frame.rect(_shown_area(page))
    raw = _text_dict(page).get("blocks", [])
    # Margins are taken as even: the far one mirrors where text running the
    # same way starts. Capped, because text that only starts mid-page - a
    # label up the side, a form field - is not a sign of a huge margin; the
    # mirror once left such a line almost no room at all.
    dx, dy = frame.direction
    lefts = [frame.rect(line["bbox"]).x0 for block in raw if block.get("type") == 0
             for line in block.get("lines", [])
             if abs(line["dir"][0] - dx) < 1e-3 and abs(line["dir"][1] - dy) < 1e-3]
    margin = min(108.0, max(18.0, (min(lefts) if lefts else area.x0 + 72.0) - area.x0))
    limit = area.x1 - margin
    if next_x is not None:
        limit = min(limit, next_x - 0.3 * size)

    for bi, block in enumerate(raw):
        if block.get("type") != 0:
            continue
        for li, other in enumerate(block.get("lines", [])):
            box = frame.rect(other["bbox"])
            if (bi, li) != (b_index, l_index) and on_row(box) and box.x0 >= end - 1:
                limit = min(limit, box.x0 - 0.3 * size)

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        box = frame.rect(drawing["rect"])
        if not on_row(box):
            continue
        if box.x0 >= end - 0.5:                     # a rule or border to the right
            limit = min(limit, box.x0 - 1.5)
        elif box.x0 <= x0 and end + 0.5 <= box.x1 < area.x1 - 1:
            limit = min(limit, box.x1 - 1.5)        # a cell or box drawn around it
    return limit


def _ink(page: fitz.Page) -> int:
    """How many visible characters the page has."""
    return sum(1 for block in page.get_text("rawdict").get("blocks", [])
               for line in block.get("lines", []) for span in line["spans"]
               for char in span["chars"] if not char["c"].isspace())


def _redact_spans(doc: fitz.Document, page_no: int, spans: list[dict],
                  frame: _Frame = _UPRIGHT) -> None:
    """Remove the glyphs of exactly these runs, whose boxes are in `frame`.

    MuPDF removes a glyph once a redaction covers about a tenth of its box, and
    that box runs from an em above the baseline to a third of one below. So a
    line's bounding box overlaps the glyphs of the rows above and below it
    wherever lines sit close, and redacting it wiped their text. A thin band
    through the middle of each run's own glyphs reaches nothing on another row.

    It is tried on a copy of the page first. For text at quarter turns the
    band is exact, but MuPDF tests slanted glyphs against their upright
    bounding box, so at other angles it can catch letters on the rows beside
    it. An edit is refused rather than allowed to take someone's words.
    """
    areas = []
    own = 0
    for span in spans:
        x0, _, x1, bottom = span["bbox"]
        size = span.get("size", 11.0)
        baseline = span.get("origin", (x0, bottom))[1]
        top, foot = baseline - 0.45 * size, baseline - 0.35 * size
        # MuPDF judges a slanted redaction by its upright bounding box too, and
        # one long band at an angle has a box tall enough to reach the next
        # rows. Short pieces each have a small box.
        step = (x1 - x0) if frame.square else max(1.0, 0.35 * size)
        start = x0
        while start < x1 - 1e-6:
            areas.append(frame.area(fitz.Rect(start, top, min(x1, start + step), foot)))
            start += step
        own += sum(1 for c in span.get("text", "") if not c.isspace())

    try:
        trial = fitz.open()
        trial.insert_pdf(doc, from_page=page_no, to_page=page_no, annots=False)
        scratch = trial[0]
        before = _ink(scratch)
        for area in areas:
            scratch.add_redact_annot(area)
        scratch.apply_redactions(**_REDACT_KW)
        lost = before - _ink(scratch)
    except Exception:
        lost = own            # the trial is a precaution; never let it block an edit
    if lost > own:
        raise EditRefused("That text sits too close to other text to be changed "
                          "without erasing some of it, so nothing was changed.")

    _apply_redactions(doc, page_no, areas)


def _draw_runs(page: fitz.Page, runs: list[dict], start_x: float,
               binder: FontBinder, available: float, frame: _Frame = _UPRIGHT,
               anchor: float | None = None) -> dict:
    """Draw styled runs left to right on their own baselines, within one row.

    Wording wider than `available` is drawn smaller until it fits. Wrapping it
    put the tail over the row beneath; letting it run on pushed it past the
    page edge, where it was invisible. With an `anchor` the runs end there
    instead of starting at `start_x`: right-aligned text.
    """
    fonts_before = fontmod.page_font_xrefs(page.parent, page.number)
    prepared = []
    total = 0.0
    for run in runs:
        if not run["text"]:
            continue
        alias, font = binder.bind(page, run["font"], run["text"])
        width = font.text_length(run["text"], fontsize=run["size"])
        prepared.append({**run, "alias": alias, "width": width})
        total += width
    if not prepared:
        return {"ok": True, "scale": 1.0, "shrunk": False, "width": 0.0}

    scale = min(1.0, available / total) if total > 0 else 1.0
    pen = start_x if anchor is None else anchor - total * scale
    start_x = pen
    for run in prepared:
        frame.put(page, (pen, run["baseline"]), run["text"],
                  fontname=run["alias"], fontsize=run["size"] * scale,
                  color=_color_tuple(run["color"]),
                  render_mode=3 if run.get("invisible") else 0)
        pen += run["width"] * scale

    fontmod.repair_inserted_fonts(page.parent, page.number, fonts_before)
    return {
        "ok": True,
        "scale": round(scale, 3),
        "shrunk": scale < 0.999,
        "width": round(pen - start_x, 2),
    }


def _alignment(block: dict) -> str:
    lines = [ln for ln in block.get("lines", []) if ln.get("spans")]
    if len(lines) < 2:
        return "left"
    left = block["bbox"][0]
    right = block["bbox"][2]
    lefts = [round(ln["bbox"][0] - left, 1) for ln in lines]
    rights = [round(right - ln["bbox"][2], 1) for ln in lines]
    left_ragged = max(lefts) - min(lefts) > 1.5
    right_ragged = max(rights) - min(rights) > 1.5
    if left_ragged and not right_ragged:
        return "right"
    if left_ragged and right_ragged:
        return "center"
    if not right_ragged:
        return "justify"
    return "left"


def _dominant_style(block: dict) -> dict:
    tally: dict[tuple, float] = {}
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text.strip():
                continue
            bold, italic = _flags(span)
            key = (span.get("font", ""), round(span.get("size", 11.0), 1),
                   span.get("color", 0), bold, italic)
            tally[key] = tally.get(key, 0) + len(text)
    if not tally:
        return {"font": "", "size": 11.0, "color": 0, "bold": False, "italic": False}
    font, size, color, bold, italic = max(tally.items(), key=lambda kv: kv[1])[0]
    return {"font": font, "size": size, "color": color, "bold": bold, "italic": italic}


def _line_spacing(block: dict, size: float) -> float:
    lines = [ln for ln in block.get("lines", []) if ln.get("spans")]
    if len(lines) < 2:
        return 1.15
    tops = [ln["bbox"][1] for ln in lines]
    gaps = [b - a for a, b in zip(tops, tops[1:]) if b - a > 0]
    if not gaps or size <= 0:
        return 1.15
    return max(0.9, min(2.5, (sum(gaps) / len(gaps)) / size))


def _wrap(font: fitz.Font, size: float, text: str, max_width: float) -> list[tuple[list[str], bool]]:
    """Greedy word wrap. Returns (words, is_last_line_of_paragraph) per line."""
    out: list[tuple[list[str], bool]] = []
    for paragraph in (text.splitlines() or [""]):
        words = paragraph.split()
        if not words:
            out.append(([], True))
            continue
        current: list[str] = []
        wrapped: list[list[str]] = []
        for word in words:
            trial = " ".join(current + [word])
            if current and font.text_length(trial, fontsize=size) > max_width:
                wrapped.append(current)
                current = [word]
            else:
                current.append(word)
        if current:
            wrapped.append(current)
        for index, line in enumerate(wrapped):
            out.append((line, index == len(wrapped) - 1))
    return out


def _typeset(page: fitz.Page, rect: fitz.Rect, text: str, style: dict, align: str,
             spacing: float, binder: FontBinder, min_scale: float = 0.85,
             max_bottom: float | None = None, frame: _Frame = _UPRIGHT) -> dict:
    """Lay out wrapped, aligned text inside `rect` using the page content stream.

    Grows the box downward into free space first, then shrinks the font -- but
    only mildly. Text that still does not fit is drawn at the floor size and
    flagged as overflowing, which reads far better than silently shrinking a
    paragraph to an illegible size to make it fit. `rect` and `max_bottom` are
    in `frame`, which is how a turned paragraph wraps the way it reads.
    """
    alias, font = binder.bind(page, style["font"], text)
    width = rect.width
    ceiling = max_bottom if max_bottom is not None else frame.rect(_shown_area(page)).y1 - 4
    available = max(rect.height, ceiling - rect.y0)

    scale = 1.0
    lines = _wrap(font, style["size"], text, width)
    for step in range(8):
        scale = 1.0 - step * 0.025
        if scale < min_scale:
            scale = min_scale
        size = style["size"] * scale
        lines = _wrap(font, size, text, width)
        if len(lines) * size * spacing <= available or scale <= min_scale:
            break

    size = style["size"] * scale
    space_width = font.text_length(" ", fontsize=size)
    color = _color_tuple(style["color"])
    baseline = rect.y0 + font.ascender * size

    for words, is_last in lines:
        if words:
            widths = [font.text_length(w, fontsize=size) for w in words]
            natural = sum(widths) + space_width * (len(words) - 1)
            gap = space_width
            if align == "right":
                pen = rect.x1 - natural
            elif align == "center":
                pen = rect.x0 + (width - natural) / 2
            else:
                pen = rect.x0
                if align == "justify" and not is_last and len(words) > 1:
                    gap = space_width + (width - natural) / (len(words) - 1)
            for word, word_width in zip(words, widths):
                frame.put(page, (pen, baseline), word, fontname=alias,
                          fontsize=size, color=color)
                pen += word_width + gap
        baseline += size * spacing

    used = len(lines) * size * spacing
    return {
        "ok": True,
        "scale": round(scale, 3),
        "shrunk": scale < 0.999,
        "overflow": used > available + 0.5,
        "align": align,
        "height": round(used, 2),
    }


def _max_bottom(page: fitz.Page, rect: fitz.Rect, skip_block: int,
                frame: _Frame = _UPRIGHT) -> float:
    """Lowest y an edited paragraph may grow to before hitting other content.

    Without this a paragraph that gains lines just overprints whatever sits
    below it -- typically a table. Must be called before the redaction, while
    the neighbouring content is still on the page. `rect` and the answer are
    in `frame`; "below" is below as the paragraph reads.
    """
    limit = frame.rect(_shown_area(page)).y1 - 4.0
    def consider(other: fitz.Rect):
        nonlocal limit
        overlaps_x = other.x1 > rect.x0 + 1 and other.x0 < rect.x1 - 1
        if overlaps_x and other.y0 >= rect.y1 - 0.5:
            limit = min(limit, other.y0 - 1.0)
    for index, block in enumerate(_text_dict(page).get("blocks", [])):
        if index != skip_block:
            consider(frame.rect(block["bbox"]))
    try:
        for drawing in page.get_drawings():
            consider(frame.rect(drawing["rect"]))
    except Exception:
        pass
    return max(limit, rect.y1)


def edit_block(doc: fitz.Document, block_id: str, new_text: str) -> dict:
    """Rewrite a paragraph, re-wrapping inside its original bounding box."""
    parts = parse_id(block_id)
    if len(parts) != 2:
        raise ValueError("That paragraph reference is not valid.")
    page_no, b_index = parts
    page = _page(doc, page_no)
    block, _ = _locate(page, b_index)
    lines = [ln for ln in block.get("lines", []) if ln.get("spans")]
    every = [span for ln in lines for span in ln["spans"]]
    unreadable = _unreadable_fonts(page)
    if any(not _readable(span, unreadable) for span in every if span.get("text")):
        raise EditRefused("Some of this paragraph's text is in a font that does not record "
                          "which letters it draws, so it can't be rewritten without "
                          "garbling it. Nothing was changed.")
    if _right_to_left(new_text) or any(_right_to_left(s.get("text", "")) for s in every):
        raise EditRefused("Right-to-left text can't be edited here yet, so nothing was "
                          "changed.")
    ways = {(round(ln["dir"][0], 3), round(ln["dir"][1], 3)) for ln in lines}
    if len(ways) > 1:
        raise EditRefused("This paragraph has text running in different directions, so "
                         "it can't be rewritten as one. Edit its lines one at a time.")

    # Laid out in the paragraph's own frame, so a turned paragraph wraps the
    # way it reads rather than the way the page is stored.
    frame = _Frame(lines[0]["dir"] if lines else (1, 0))
    local = _local_block(block, frame)
    style = _dominant_style(local)
    align = _alignment(local)
    spacing = _line_spacing(local, style["size"])
    rect = fitz.Rect(block["bbox"]) if frame.upright else fitz.Rect(local["bbox"])
    max_bottom = _max_bottom(page, rect, b_index, frame)

    page = None          # let go: the redaction reloads the page
    _redact_spans(doc, page_no, [span for line in local["lines"]
                                 for span in line["spans"] if span.get("text")], frame)

    page = doc[page_no]
    binder = FontBinder(doc)
    fonts_before = fontmod.page_font_xrefs(doc, page_no)
    result = _typeset(page, rect, new_text, style, align, spacing, binder,
                      max_bottom=max_bottom, frame=frame)
    fontmod.repair_inserted_fonts(doc, page_no, fonts_before)
    return result


def delete_region(doc: fitz.Document, page_no: int, rect: list[float]) -> dict:
    """Remove all text inside a rectangle, leaving images and line art intact."""
    _apply_redactions(doc, page_no, [fitz.Rect(rect)])
    return {"ok": True}


def add_text(doc: fitz.Document, page_no: int, rect: list[float], text: str,
             font_name: str = "Calibri", size: float = 11.0,
             color: tuple[float, float, float] = (0, 0, 0), align: str = "left",
             bold: bool = False, italic: bool = False, spacing: float = 1.15) -> dict:
    """Draw a new block of text into the page content stream."""
    page = doc[page_no]
    name = font_name
    if bold and "bold" not in name.lower():
        name += "-Bold"
    if italic and "italic" not in name.lower():
        name += "-Italic"
    style = {"font": name, "size": size,
             "color": (int(color[0] * 255) << 16) | (int(color[1] * 255) << 8) | int(color[2] * 255)}
    binder = FontBinder(doc)
    fonts_before = fontmod.page_font_xrefs(doc, page_no)
    # Set the way the page is shown: on a turned page "across" runs along
    # what the page stores as up or down.
    turn = page.derotation_matrix
    frame = _Frame(fitz.Point(1, 0) * fitz.Matrix(turn.a, turn.b, turn.c, turn.d, 0, 0))
    result = _typeset(page, frame.rect(rect), text, style, align, spacing, binder,
                      min_scale=0.6, frame=frame)
    fontmod.repair_inserted_fonts(doc, page_no, fonts_before)
    return result


def _swap(text: str, find: str, replace: str, match_case: bool) -> str:
    """Replace every occurrence in one string, optionally ignoring case."""
    if match_case:
        return text.replace(find, replace)
    out = []
    lowered = text.lower()
    needle = find.lower()
    at = 0
    while True:
        found = lowered.find(needle, at)
        if found < 0:
            break
        out.append(text[at:found])
        out.append(replace)
        at = found + len(needle)
    out.append(text[at:])
    return "".join(out)


def replace_all(doc: fitz.Document, find: str, replace: str,
                match_case: bool = True) -> dict:
    """Find/replace across the document.

    Each text run is rewritten at most once, and every occurrence inside it is
    swapped in that single pass. Revisiting a run would never terminate when
    the replacement itself contains the search text (replacing "ABC" with
    "ABC-ABC", say), and re-running the search after each edit would keep
    rediscovering the text it had just written.

    A match is only rewritten when it falls entirely within one styled run;
    matches split across styling boundaries are reported as skipped rather
    than silently mangled.
    """
    if not find:
        raise ValueError("Nothing to find.")
    binder = FontBinder(doc)
    changed = 0
    skipped = 0
    needle = find if match_case else find.lower()

    for page_no in range(doc.page_count):
        done: set[tuple[float, float]] = set()
        while True:
            hit = None
            for block in layout(doc[page_no])["blocks"]:
                for line in block["lines"]:
                    for span in line["spans"]:
                        key = (round(span["bbox"][0], 1), round(span["bbox"][1], 1))
                        if key in done:
                            continue
                        haystack = span["text"] if match_case else span["text"].lower()
                        if needle in haystack:
                            hit = (span, key)
                            break
                    if hit:
                        break
                if hit:
                    break
            if not hit:
                break
            span, key = hit
            done.add(key)  # this run is finished, whatever happens next
            updated = _swap(span["text"], find, replace, match_case)
            if updated == span["text"]:
                continue
            # Only an edit turned down before it changed anything is skipped;
            # anything else fails the whole replacement, which is then undone.
            try:
                edit_span(doc, span["id"], updated, binder)
                changed += 1
            except EditRefused:
                skipped += 1

    remaining = 0
    for page_no in range(doc.page_count):
        text = "\n".join(span["text"] for block in _text_dict(doc[page_no]).get("blocks", [])
                         for line in block.get("lines", []) for span in line["spans"])
        remaining += (text if match_case else text.lower()).count(needle)
    return {"replaced": changed, "skipped": skipped, "remaining": remaining}
