"""In-place text editing for PDFs.

A PDF stores positioned glyph draws, not editable paragraphs, so "editing text"
means removing the original glyphs and re-typesetting replacements. Two units:

  span  - rewrite one run of same-styled text. The whole *line* is redacted and
          redrawn span-by-span so neighbouring runs reflow instead of being
          clipped by an overlapping redaction box.
  block - rewrite a paragraph, re-wrapping across lines inside its bounding box.

Tuned for PDFs exported from Word, which produce clean, well-separated text runs.
"""
from __future__ import annotations

import html as html_mod
import re

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
        if cached and self._covers(cached[1], text):
            return cached

        spec = self.resolver.resolve(page, span_font)
        candidates = [spec]
        if spec["kind"] != "base14":
            candidates.append({
                "kind": "base14",
                "base14": fontmod.base14_for(span_font, spec["bold"], spec["italic"]),
            })

        for candidate in candidates:
            try:
                alias, font = self._register(page, candidate)
            except Exception:
                continue
            if self._covers(font, text):
                self._bound[key] = (alias, font)
                return alias, font

        alias, font = self._register(page, {"kind": "base14", "base14": "helv"})
        self._bound[key] = (alias, font)
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
    def _covers(font: fitz.Font, text: str) -> bool:
        """True if the font has a glyph for every non-space character."""
        for ch in text:
            if ch in " \t\r\n":
                continue
            try:
                if not font.has_glyph(ord(ch)):
                    return False
            except Exception:
                return False
        return True


def layout(page: fitz.Page) -> dict:
    """Editable text structure for one page: blocks -> lines -> spans."""
    raw = page.get_text("dict")
    blocks = []
    for b_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        lines = []
        for l_index, line in enumerate(block.get("lines", [])):
            spans = []
            for s_index, span in enumerate(line.get("spans", [])):
                if not span.get("text"):
                    continue
                bold, italic = _flags(span)
                spans.append({
                    "id": f"{page.number}:{b_index}:{l_index}:{s_index}",
                    "text": span["text"],
                    "bbox": list(span["bbox"]),
                    "origin": list(span.get("origin", (span["bbox"][0], span["bbox"][3]))),
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 11.0), 2),
                    "color": span.get("color", 0),
                    "bold": bold,
                    "italic": italic,
                })
            if spans:
                lines.append({
                    "id": f"{page.number}:{b_index}:{l_index}",
                    "bbox": list(line["bbox"]),
                    "dir": list(line.get("dir", (1, 0))),
                    "spans": spans,
                })
        if lines:
            blocks.append({
                "id": f"{page.number}:{b_index}",
                "bbox": list(block["bbox"]),
                "lines": lines,
                "text": "\n".join("".join(s["text"] for s in ln["spans"]) for ln in lines),
            })
    return {"page": page.number, "blocks": blocks}


def _locate(page: fitz.Page, target_block: int, target_line: int | None = None):
    raw = page.get_text("dict")
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


def _horizontal(line: dict) -> bool:
    direction = line.get("dir", (1, 0))
    return abs(direction[0]) > 0.999


def edit_span(doc: fitz.Document, span_id: str, new_text: str,
              binder: FontBinder | None = None) -> dict:
    """Replace one text run, reflowing the rest of its line."""
    parts = parse_id(span_id)
    if len(parts) != 4:
        raise ValueError("That text reference is not valid.")
    page_no, b_index, l_index, s_index = parts
    page = _page(doc, page_no)
    block, line = _locate(page, b_index, l_index)
    spans = [s for s in line["spans"] if s.get("text")]
    if s_index >= len(line["spans"]):
        raise ValueError("Text run no longer exists on this page.")
    if not _horizontal(line):
        raise ValueError("Rotated or vertical text can't be edited in place yet.")

    target = line["spans"][s_index]
    rebuilt = []
    for span in spans:
        text = new_text if span is target else span["text"]
        rebuilt.append({
            "text": text,
            "font": span.get("font", ""),
            "size": span.get("size", 11.0),
            "color": span.get("color", 0),
        })

    binder = binder or FontBinder(doc)
    baseline_y = target.get("origin", (0, line["bbox"][3]))[1]
    start_x = line["bbox"][0]

    # How much room this line actually has.
    right_edge = max(block["bbox"][2], line["bbox"][2])
    if right_edge - start_x < 40:
        right_edge = page.rect.x1 - 36
    available = max(40.0, right_edge - start_x)
    spacing = _line_spacing(block, _dominant_style(block)["size"])

    # If the new wording no longer fits the line, re-wrap the whole paragraph.
    # Wrapping just this line would lay it over the lines beneath it, and
    # drawing it anyway would push the tail off the page, where it is invisible
    # and lost from the extracted text too.
    probe = FontBinder(doc)
    needed = 0.0
    for run in rebuilt:
        if not run["text"]:
            continue
        _, font = probe.bind(page, run["font"], run["text"])
        needed += font.text_length(run["text"], fontsize=run["size"])
    if needed > available * 1.02 and len(block.get("lines", [])) >= 1:
        return _reflow_block(doc, page_no, b_index, l_index, s_index, new_text)

    ceiling = _max_bottom(page, fitz.Rect(line["bbox"]), b_index)

    rect = fitz.Rect(line["bbox"])
    rect.y0 -= 1.0
    rect.y1 += 1.0
    page.add_redact_annot(rect)
    page.apply_redactions(**_REDACT_KW)

    page = doc[page_no]
    return _draw_runs(page, rebuilt, start_x, baseline_y, binder,
                      available=available, spacing=spacing, max_bottom=ceiling)


def _reflow_block(doc: fitz.Document, page_no: int, b_index: int,
                  l_index: int, s_index: int, new_text: str) -> dict:
    """Re-wrap a whole paragraph with one of its runs replaced."""
    page = doc[page_no]
    block, _ = _locate(page, b_index)
    pieces = []
    for li, line in enumerate(block.get("lines", [])):
        for si, span in enumerate(line.get("spans", [])):
            text = new_text if (li == l_index and si == s_index) else span.get("text", "")
            pieces.append(text)
        pieces.append(" ")          # the wrapped break becomes a space
    paragraph = " ".join("".join(pieces).split())
    result = edit_block(doc, "%d:%d" % (page_no, b_index), paragraph)
    result["reflowed"] = True
    return result


def _tokens(text: str):
    """Split into words that keep their trailing space, so wrapping is clean."""
    found = re.findall(r"\S+\s*|\s+", text)
    return found or ([text] if text else [])


def _draw_runs(page: fitz.Page, runs: list[dict], start_x: float, baseline_y: float,
               binder: FontBinder, available: float, spacing: float = 1.15,
               max_bottom: float | None = None) -> dict:
    """Draw styled runs from a baseline, wrapping rather than running off the page.

    Text longer than the space it replaced used to be squeezed to 55% and drawn
    anyway, so the tail slid past the right edge and simply vanished -- missing
    from the page and from the extracted text. Now it shrinks only slightly,
    then wraps onto further lines, and reports if it still will not fit.
    """
    fonts_before = fontmod.page_font_xrefs(page.parent, page.number)
    prepared = []
    total = 0.0
    for run in runs:
        if not run["text"]:
            continue
        alias, font = binder.bind(page, run["font"], run["text"])
        width = font.text_length(run["text"], fontsize=run["size"])
        prepared.append({**run, "alias": alias, "font": font, "width": width})
        total += width
    if not prepared:
        return {"ok": True, "scale": 1.0, "shrunk": False, "wrapped": False,
                "overflow": False, "width": 0.0, "lines": 0}

    # A small squeeze keeps a near miss on one line; beyond that, wrap.
    scale = 1.0
    if total > available:
        scale = max(0.9, available / total)

    body = max(run["size"] for run in prepared) * scale
    line_height = body * spacing
    ceiling = max_bottom if max_bottom is not None else page.rect.y1 - 4

    pen = start_x
    baseline = baseline_y
    lines = 1
    overflow = False

    for run in prepared:
        size = run["size"] * scale
        color = _color_tuple(run["color"])
        for token in _tokens(run["text"]):
            width = run["font"].text_length(token, fontsize=size)
            if pen + width > start_x + available and pen > start_x:
                if baseline + line_height > ceiling:
                    overflow = True          # nowhere left to put it
                    break
                baseline += line_height
                pen = start_x
                lines += 1
                token = token.lstrip()
                width = run["font"].text_length(token, fontsize=size)
            if not token:
                continue
            page.insert_text(fitz.Point(pen, baseline), token, fontname=run["alias"],
                             fontsize=size, color=color, render_mode=0)
            pen += width
        if overflow:
            break

    fontmod.repair_inserted_fonts(page.parent, page.number, fonts_before)

    return {
        "ok": True,
        "scale": round(scale, 3),
        "shrunk": scale < 0.999,
        "wrapped": lines > 1,
        "overflow": overflow,
        "lines": lines,
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
             max_bottom: float | None = None) -> dict:
    """Lay out wrapped, aligned text inside `rect` using the page content stream.

    Grows the box downward into free space first, then shrinks the font -- but
    only mildly. Text that still does not fit is drawn at the floor size and
    flagged as overflowing, which reads far better than silently shrinking a
    paragraph to an illegible size to make it fit.
    """
    alias, font = binder.bind(page, style["font"], text)
    width = rect.width
    ceiling = max_bottom if max_bottom is not None else page.rect.y1 - 4
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
                page.insert_text(fitz.Point(pen, baseline), word, fontname=alias,
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


def _max_bottom(page: fitz.Page, rect: fitz.Rect, skip_block: int) -> float:
    """Lowest y an edited paragraph may grow to before hitting other content.

    Without this a paragraph that gains lines just overprints whatever sits
    below it -- typically a table. Must be called before the redaction, while
    the neighbouring content is still on the page.
    """
    limit = page.rect.y1 - 4.0
    def consider(other: fitz.Rect):
        nonlocal limit
        overlaps_x = other.x1 > rect.x0 + 1 and other.x0 < rect.x1 - 1
        if overlaps_x and other.y0 >= rect.y1 - 0.5:
            limit = min(limit, other.y0 - 1.0)
    for index, block in enumerate(page.get_text("dict").get("blocks", [])):
        if index != skip_block:
            consider(fitz.Rect(block["bbox"]))
    try:
        for drawing in page.get_drawings():
            consider(fitz.Rect(drawing["rect"]))
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
    if not any(_horizontal(ln) for ln in block.get("lines", []) if ln.get("spans")):
        raise ValueError("Rotated or vertical text can't be edited in place yet.")

    style = _dominant_style(block)
    align = _alignment(block)
    spacing = _line_spacing(block, style["size"])
    rect = fitz.Rect(block["bbox"])
    max_bottom = _max_bottom(page, rect, b_index)

    redact = fitz.Rect(rect)
    redact.y0 -= 1.0
    redact.y1 += 1.0
    page.add_redact_annot(redact)
    page.apply_redactions(**_REDACT_KW)

    page = doc[page_no]
    binder = FontBinder(doc)
    fonts_before = fontmod.page_font_xrefs(doc, page_no)
    result = _typeset(page, rect, new_text, style, align, spacing, binder,
                      max_bottom=max_bottom)
    fontmod.repair_inserted_fonts(doc, page_no, fonts_before)
    return result


def delete_region(doc: fitz.Document, page_no: int, rect: list[float]) -> dict:
    """Remove all text inside a rectangle, leaving images and line art intact."""
    page = doc[page_no]
    page.add_redact_annot(fitz.Rect(rect))
    page.apply_redactions(**_REDACT_KW)
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
    result = _typeset(page, fitz.Rect(rect), text, style, align, spacing, binder, min_scale=0.6)
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
            try:
                edit_span(doc, span["id"], updated, binder)
                changed += 1
            except Exception:
                skipped += 1

    remaining = 0
    for page_no in range(doc.page_count):
        text = doc[page_no].get_text()
        remaining += (text if match_case else text.lower()).count(needle)
    return {"replaced": changed, "skipped": skipped, "remaining": remaining}
