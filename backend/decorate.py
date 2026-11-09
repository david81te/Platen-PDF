"""Watermarks, page numbers, headers, footers and page backgrounds."""
from __future__ import annotations

import datetime
import os
import re

import pymupdf as fitz

from . import fonts as fontmod

CORNERS = ("top-left", "top-center", "top-right",
           "bottom-left", "bottom-center", "bottom-right")


def _selected(doc: fitz.Document, pages: list[int] | None) -> list[int]:
    if not pages:
        return list(range(doc.page_count))
    return [p for p in pages if 0 <= p < doc.page_count]


def _bind(page: fitz.Page, font_name: str) -> tuple[str, fitz.Font]:
    path = fontmod.local_font_path(font_name)
    if path:
        try:
            page.insert_font(fontname="ZD1", fontfile=path)
            return "ZD1", fitz.Font(fontfile=path)
        except Exception:
            pass
    alias = fontmod.base14_for(font_name)
    page.insert_font(fontname=alias)
    return alias, fitz.Font(fontname=alias)


def watermark_text(doc: fitz.Document, text: str, pages: list[int] | None = None,
                   size: float = 48, color: tuple = (0.6, 0.6, 0.6),
                   opacity: float = 0.25, angle: float = 45,
                   font_name: str = "Helvetica", behind: bool = False) -> dict:
    count = 0
    for index in _selected(doc, pages):
        page = doc[index]
        before = fontmod.page_font_xrefs(doc, index)
        alias, font = _bind(page, font_name)
        width = font.text_length(text, fontsize=size)
        centre = fitz.Point(page.rect.width / 2, page.rect.height / 2)
        page.insert_text(
            fitz.Point(centre.x - width / 2, centre.y + size * 0.35),
            text, fontname=alias, fontsize=size, color=color,
            fill_opacity=opacity, stroke_opacity=opacity,
            morph=(centre, fitz.Matrix(angle)), overlay=not behind,
        )
        fontmod.repair_inserted_fonts(doc, index, before)
        count += 1
    return {"pages": count}


def watermark_image(doc: fitz.Document, image_path: str, pages: list[int] | None = None,
                    scale: float = 0.5, behind: bool = False) -> dict:
    count = 0
    for index in _selected(doc, pages):
        page = doc[index]
        rect = page.rect
        width = rect.width * scale
        height = rect.height * scale
        dx = (rect.width - width) / 2
        dy = (rect.height - height) / 2
        page.insert_image(fitz.Rect(dx, dy, dx + width, dy + height),
                          filename=image_path, overlay=not behind, alpha=-1)
        count += 1
    return {"pages": count}


# Page numbers, headers and footers, and Bates numbers are each one "kind" of
# stamp. Every stamp is wrapped in marked content naming its kind, as a
# pagination artifact - which is also what tells a screen reader to skip it -
# so applying a kind again replaces what it stamped before instead of printing
# a second set on top, and Remove takes exactly that and nothing else.
STAMP_KINDS = ("numbers", "headfoot", "bates")


def _stamp_marker(kind: str) -> tuple[bytes, bytes]:
    return (b"/Artifact <</Type /Pagination /PlatenStamp /%s>> BDC\n" % kind.encode(),
            b"\nEMC\n")


def _stamp_start(kind: str) -> re.Pattern:
    return re.compile(rb"/Artifact\s*<<(?:(?!>>).)*?/PlatenStamp\s*/" + kind.encode() +
                      rb"(?![A-Za-z])(?:(?!>>).)*>>\s*BDC", re.DOTALL)


_WORD = re.compile(rb"[^\s()<>\[\]{}/%]+")


def _marked_end(data: bytes, at: int) -> int | None:
    """Just past the EMC that closes marked content whose body starts at `at`.

    Read as PDF tokens, so an "EMC" inside a string is text, not the operator.
    A stamp is written with hex strings, but any redaction on the page - an
    edit, a find and replace - rewrites the stream with literal ones, and a
    pattern match then stopped inside "(Prepared by EMC Labs)", cutting the
    stream in half.
    """
    depth, i, end = 1, at, len(data)
    while i < end:
        c = data[i:i + 1]
        if c == b"(":                                   # literal string
            level, i = 1, i + 1
            while i < end and level:
                ch = data[i:i + 1]
                if ch == b"\\":
                    i += 2
                    continue
                level += (ch == b"(") - (ch == b")")
                i += 1
        elif c == b"<" and data[i + 1:i + 2] != b"<":   # hex string
            close = data.find(b">", i)
            i = close + 1 if close >= 0 else end
        elif c == b"%":                                 # comment
            line = re.compile(rb"[\r\n]").search(data, i)
            i = line.end() if line else end
        elif c == b"/":                                 # a name, never an operator
            word = _WORD.match(data, i + 1)
            i = word.end() if word else i + 1
        else:
            word = _WORD.match(data, i)
            if not word:
                i += 1
                continue
            i = word.end()
            if word.group() in (b"BDC", b"BMC"):
                depth += 1
            elif word.group() == b"EMC":
                depth -= 1
                if depth == 0:
                    return i
    return None


def _strip_stamps(data: bytes, kind: str) -> tuple[bytes, int]:
    start = _stamp_start(kind)
    out, at, hits = [], 0, 0
    while True:
        found = start.search(data, at)
        if not found:
            break
        stop = _marked_end(data, found.end())
        if stop is None:
            break                       # unterminated: leave the rest alone
        out.append(data[at:found.start()])
        at = stop
        hits += 1
    out.append(data[at:])
    return b"".join(out), hits


def remove_stamps(doc: fitz.Document, kind: str, pages: list[int] | None = None) -> dict:
    """Take away what an earlier stamp of this kind put on the pages."""
    if kind not in STAMP_KINDS:
        raise ValueError("Unknown kind of stamp.")
    count = 0
    for index in _selected(doc, pages):
        found = False
        for xref in doc[index].get_contents():
            data = doc.xref_stream(xref) or b""
            cleaned, hits = _strip_stamps(data, kind)
            if hits:
                doc.update_stream(xref, cleaned)
                found = True
        count += found
    return {"pages": count}


def _upright(page: fitz.Page, point: fitz.Point) -> tuple[fitz.Point, int]:
    """Where to insert text so it reads upright at `point` as the page is shown.

    insert_text works in the page's unrotated coordinates. Placing by the
    shown size alone put page numbers off the page entirely on a page turned
    90 degrees - most scanned landscape pages - and upside down in the wrong
    corner on one turned 180.
    """
    return point * page.derotation_matrix, page.rotation


def _fill(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace("{%s}" % key, value)
    return template


def bates_label(number: int, prefix: str = "", digits: int = 6, suffix: str = "") -> str:
    return "%s%s%s" % (prefix, str(int(number)).zfill(max(1, int(digits))), suffix)


def stamp(doc: fitz.Document, kind: str, fields: dict[str, str],
          pages: list[int] | None = None, size: float = 9,
          color: tuple = (0.2, 0.2, 0.2), font_name: str = "Helvetica",
          margin: float = 36, start_at: int = 1, skip_first: bool = False,
          bates: dict | None = None, file_name: str = "") -> dict:
    """Stamp text in any of the six margin positions of each page.

    `fields` maps a position to a template. Placeholders: {page} the page's
    number counting from `start_at`, {pages} the last such number, {n} the
    position within the pages being stamped, {bates} the Bates number,
    {date}, {file} and {title}. Replaces this kind's earlier stamps.
    """
    if kind not in STAMP_KINDS:
        raise ValueError("Unknown kind of stamp.")
    fields = {pos: text for pos, text in (fields or {}).items()
              if pos in CORNERS and text and text.strip()}
    if not fields:
        raise ValueError("There is nothing to stamp.")
    today = datetime.date.today()
    shared = {
        "pages": str(doc.page_count + start_at - 1),
        "date": "%s %d, %d" % (today.strftime("%B"), today.day, today.year),
        "file": file_name,
        "title": (doc.metadata or {}).get("title") or os.path.splitext(file_name)[0],
    }
    # Asked before anything is removed: a header of only {file} on a document
    # never saved would otherwise take the old header away and add nothing.
    sample = dict(shared, page="1", n="1", bates="1")
    if not any(_fill(template, sample).strip() for template in fields.values()):
        raise ValueError("That comes out empty for this document - {file} and {title} "
                         "have nothing to show until it is saved or given a title.")
    remove_stamps(doc, kind)

    targets = [i for i in _selected(doc, pages) if not (skip_first and i == 0)]
    bates = bates or {}
    first_bates = int(bates.get("start", 1))
    open_mark, close_mark = _stamp_marker(kind)
    stamped = 0

    for ordinal, index in enumerate(targets):
        page = doc[index]
        before = fontmod.page_font_xrefs(doc, index)
        alias, font = _bind(page, font_name)
        values = dict(shared, page=str(index + start_at), n=str(ordinal + start_at),
                      bates=bates_label(first_bates + ordinal, bates.get("prefix", ""),
                                        bates.get("digits", 6), bates.get("suffix", "")))
        shown = page.rect
        shape = page.new_shape()
        existing = set(page.get_contents())
        for position, template in fields.items():
            text = _fill(template, values)
            if not text.strip():
                continue           # {file} on a document never saved, say
            width = font.text_length(text, fontsize=size)
            if position.endswith("left"):
                x = margin
            elif position.endswith("right"):
                x = shown.width - margin - width
            else:
                x = (shown.width - width) / 2
            y = (margin + font.ascender * size if position.startswith("top")
                 else shown.height - margin)
            point, turn = _upright(page, fitz.Point(x, y))
            shape.insert_text(point, text, fontname=alias, fontsize=size,
                              color=color, rotate=turn)
        shape.commit()
        # Only the stream the commit added for the text is marked: the last
        # new one. With nothing to draw it adds none, and marking the last
        # stream anyway marked the page's own content as a header - which
        # Remove then erased, page and all. On a page whose content leaves a
        # q open, the commit first adds a Q of its own; that is not ours.
        added = [xref for xref in page.get_contents() if xref not in existing]
        if added:
            xref = added[-1]
            doc.update_stream(xref, open_mark + (doc.xref_stream(xref) or b"") + close_mark)
            stamped += 1
        fontmod.repair_inserted_fonts(doc, index, before)

    result = {"pages": stamped}
    if kind == "bates" and targets:
        result.update(first=bates_label(first_bates, bates.get("prefix", ""),
                                        bates.get("digits", 6), bates.get("suffix", "")),
                      last=bates_label(first_bates + len(targets) - 1, bates.get("prefix", ""),
                                       bates.get("digits", 6), bates.get("suffix", "")),
                      next=first_bates + len(targets))
    return result


def stamp_text(doc: fitz.Document, template: str, position: str = "bottom-center",
               pages: list[int] | None = None, size: float = 9,
               color: tuple = (0.2, 0.2, 0.2), font_name: str = "Helvetica",
               margin: float = 36, start_at: int = 1, skip_first: bool = False,
               kind: str = "numbers") -> dict:
    """One template in one position: the original page-number call."""
    if position not in CORNERS:
        position = "bottom-center"
    return stamp(doc, kind, {position: template}, pages, size, color, font_name,
                 margin, start_at, skip_first)


def background(doc: fitz.Document, pages: list[int] | None = None,
               color: tuple | None = None, image_path: str | None = None) -> dict:
    count = 0
    for index in _selected(doc, pages):
        page = doc[index]
        if image_path:
            page.insert_image(page.rect, filename=image_path, overlay=False)
        elif color is not None:
            page.draw_rect(page.rect, color=None, fill=color, overlay=False)
        count += 1
    return {"pages": count}
