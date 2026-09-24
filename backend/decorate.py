"""Watermarks, page numbers, headers, footers and page backgrounds."""
from __future__ import annotations

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


def _place(page: fitz.Page, position: str, margin: float, size: float,
           text_width: float) -> fitz.Point:
    rect = page.rect
    if position.endswith("left"):
        x = margin
    elif position.endswith("right"):
        x = rect.width - margin - text_width
    else:
        x = (rect.width - text_width) / 2
    y = margin + size if position.startswith("top") else rect.height - margin
    return fitz.Point(x, y)


def stamp_text(doc: fitz.Document, template: str, position: str = "bottom-center",
               pages: list[int] | None = None, size: float = 9,
               color: tuple = (0.2, 0.2, 0.2), font_name: str = "Helvetica",
               margin: float = 36, start_at: int = 1, skip_first: bool = False) -> dict:
    """Stamp a template on each page.

    Placeholders: page = absolute page number, n = position within the
    selection, pages = document total. One routine covers page numbers,
    headers and footers.
    """
    if position not in CORNERS:
        position = "bottom-center"
    targets = _selected(doc, pages)
    total = doc.page_count
    count = 0
    for ordinal, index in enumerate(targets):
        if skip_first and index == 0:
            continue
        page = doc[index]
        before = fontmod.page_font_xrefs(doc, index)
        alias, font = _bind(page, font_name)
        text = template
        text = text.replace("{page}", str(index + start_at))
        text = text.replace("{n}", str(ordinal + start_at))
        text = text.replace("{pages}", str(total))
        width = font.text_length(text, fontsize=size)
        page.insert_text(_place(page, position, margin, size, width), text,
                         fontname=alias, fontsize=size, color=color)
        fontmod.repair_inserted_fonts(doc, index, before)
        count += 1
    return {"pages": count}


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
