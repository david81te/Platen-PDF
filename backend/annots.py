"""Markup, comments, shapes, freehand ink, images and hyperlinks."""
from __future__ import annotations

import pymupdf as fitz

from .session import PdfError

MARKUP = {
    "highlight": "add_highlight_annot",
    "underline": "add_underline_annot",
    "strikeout": "add_strikeout_annot",
    "squiggly": "add_squiggly_annot",
}

STAMPS = {
    "approved": fitz.STAMP_Approved,
    "draft": fitz.STAMP_Draft,
    "final": fitz.STAMP_Final,
    "confidential": fitz.STAMP_Confidential,
    "experimental": fitz.STAMP_Experimental,
    "expired": fitz.STAMP_Expired,
    "sold": fitz.STAMP_Sold,
    "asis": fitz.STAMP_AsIs,
    "notapproved": fitz.STAMP_NotApproved,
    "forpublicrelease": fitz.STAMP_ForPublicRelease,
    "notforpublicrelease": fitz.STAMP_NotForPublicRelease,
    "forcomment": fitz.STAMP_ForComment,
    "topsecret": fitz.STAMP_TopSecret,
    "departmental": fitz.STAMP_Departmental,
}


# PyMuPDF already names each annotation type, and its numbering has caught me
# out before (12 is Redact, 13 is Stamp, not the other way round), so ask it
# rather than keeping a parallel table. Only the display wording is ours.
FRIENDLY = {
    "Text": "note",
    "FreeText": "text box",
    "StrikeOut": "strikeout",
    "FileAttachment": "attachment",
}


def type_name(annot) -> str:
    raw = annot.type[1] if len(annot.type) > 1 else str(annot.type[0])
    return FRIENDLY.get(raw, raw.lower())


def _words_in(page: fitz.Page, rect: fitz.Rect) -> list[fitz.Quad]:
    """Quads for words overlapping a rectangle, so markup follows the text."""
    quads = []
    for word in page.get_text("words"):
        box = fitz.Rect(word[0], word[1], word[2], word[3])
        if box.intersects(rect):
            overlap = (box & rect).get_area()
            if box.get_area() and overlap > 0.35 * box.get_area():
                quads.append(box.quad)
    return quads


def add_markup(doc: fitz.Document, page_no: int, kind: str, rects: list[list[float]],
               color: tuple = (1.0, 0.92, 0.23), author: str = "", note: str = "",
               snap_to_words: bool = True) -> dict:
    if kind not in MARKUP:
        raise PdfError("Unknown markup type: " + str(kind))
    page = doc[page_no]
    quads: list[fitz.Quad] = []
    for raw in rects:
        rect = fitz.Rect(raw)
        found = _words_in(page, rect) if snap_to_words else []
        quads.extend(found if found else [rect.quad])
    if not quads:
        raise PdfError("Nothing to mark up in that area.")
    annot = getattr(page, MARKUP[kind])(quads)
    annot.set_colors(stroke=color)
    if author or note:
        annot.set_info(title=author or "", content=note or "")
    annot.update()
    return {"id": annot.xref, "type": kind}


def add_note(doc: fitz.Document, page_no: int, point: list[float], text: str,
             author: str = "", color: tuple = (1.0, 0.85, 0.35)) -> dict:
    page = doc[page_no]
    annot = page.add_text_annot(fitz.Point(point), text, icon="Comment")
    annot.set_colors(stroke=color)
    annot.set_info(title=author or "", content=text)
    annot.update()
    return {"id": annot.xref, "type": "note"}


def add_textbox(doc: fitz.Document, page_no: int, rect: list[float], text: str,
                size: float = 11, color: tuple = (0, 0, 0),
                fill: tuple | None = None, border: tuple | None = None,
                align: int = 0) -> dict:
    """Editable free-text annotation, as opposed to text baked into the page.

    `border` is accepted but only applied when a fill is given: PyMuPDF refuses
    a border colour on a plain (non rich-text) free-text annotation.
    """
    page = doc[page_no]
    kwargs = {"fontsize": size, "text_color": color, "align": align}
    if fill is not None:
        kwargs["fill_color"] = fill
        if border is not None:
            kwargs["border_color"] = border
    annot = page.add_freetext_annot(fitz.Rect(rect), text, **kwargs)
    annot.update()
    return {"id": annot.xref, "type": "text box"}


def add_shape(doc: fitz.Document, page_no: int, kind: str, points: list[list[float]],
              color: tuple = (0.85, 0.1, 0.1), fill: tuple | None = None,
              width: float = 1.5, opacity: float = 1.0) -> dict:
    page = doc[page_no]
    if kind in ("rect", "square"):
        annot = page.add_rect_annot(fitz.Rect(list(points[0]) + list(points[1])))
    elif kind in ("circle", "ellipse"):
        annot = page.add_circle_annot(fitz.Rect(list(points[0]) + list(points[1])))
    elif kind == "line":
        annot = page.add_line_annot(fitz.Point(points[0]), fitz.Point(points[1]))
    elif kind == "arrow":
        annot = page.add_line_annot(fitz.Point(points[0]), fitz.Point(points[1]))
        annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
    elif kind == "polygon":
        annot = page.add_polygon_annot([fitz.Point(p) for p in points])
    else:
        raise PdfError("Unknown shape: " + str(kind))
    annot.set_colors(stroke=color, fill=fill)
    annot.set_border(width=width)
    annot.set_opacity(opacity)
    annot.update()
    return {"id": annot.xref, "type": kind}


def add_ink(doc: fitz.Document, page_no: int, strokes: list[list[list[float]]],
            color: tuple = (0.85, 0.1, 0.1), width: float = 2.0,
            opacity: float = 1.0) -> dict:
    page = doc[page_no]
    paths = [[(float(p[0]), float(p[1])) for p in stroke]
             for stroke in strokes if len(stroke) > 1]
    if not paths:
        raise PdfError("Nothing was drawn.")
    annot = page.add_ink_annot(paths)
    annot.set_colors(stroke=color)
    annot.set_border(width=width)
    annot.set_opacity(opacity)
    annot.update()
    return {"id": annot.xref, "type": "ink"}


def add_stamp(doc: fitz.Document, page_no: int, rect: list[float],
              label: str = "approved", color: tuple = (0.1, 0.5, 0.15)) -> dict:
    key = str(label).lower().replace(" ", "")
    if key not in STAMPS:
        raise PdfError("Unknown stamp: " + str(label))
    page = doc[page_no]
    annot = page.add_stamp_annot(fitz.Rect(rect), stamp=STAMPS[key])
    annot.set_colors(stroke=color)
    annot.update()
    return {"id": annot.xref, "type": "stamp"}


def add_image(doc: fitz.Document, page_no: int, rect: list[float],
              path: str | None = None, stream: bytes | None = None,
              keep_ratio: bool = True) -> dict:
    """Draw an image into the page content stream, not as an annotation."""
    page = doc[page_no]
    page.insert_image(fitz.Rect(rect), filename=path, stream=stream,
                      keep_proportion=keep_ratio, overlay=True)
    return {"ok": True}


def listing(doc: fitz.Document, page_no: int) -> list[dict]:
    page = doc[page_no]
    out = []
    for annot in page.annots():
        info = annot.info
        rect = annot.rect
        colors = annot.colors or {}
        out.append({
            "id": annot.xref,
            "type": type_name(annot),
            "is_signature": info.get("content") == "Signature",
            "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
            "content": info.get("content", ""),
            "author": info.get("title", ""),
            "modified": info.get("modDate", ""),
            "stroke": list(colors.get("stroke") or []),
            "fill": list(colors.get("fill") or []),
            "opacity": annot.opacity if annot.opacity is not None else 1.0,
        })
    return out


def _find(page: fitz.Page, annot_id: int):
    for annot in page.annots():
        if annot.xref == annot_id:
            return annot
    raise PdfError("That annotation no longer exists.")


def update(doc: fitz.Document, page_no: int, annot_id: int,
           rect: list[float] | None = None, content: str | None = None,
           color: tuple | None = None, fill: tuple | None = None,
           opacity: float | None = None, author: str | None = None) -> dict:
    page = doc[page_no]
    annot = _find(page, annot_id)
    if rect is not None:
        annot.set_rect(fitz.Rect(rect))
    if color is not None or fill is not None:
        annot.set_colors(stroke=color, fill=fill)
    if opacity is not None:
        annot.set_opacity(opacity)
    if content is not None or author is not None:
        info = annot.info
        annot.set_info(title=author if author is not None else info.get("title", ""),
                       content=content if content is not None else info.get("content", ""))
    annot.update()
    return {"id": annot.xref}


def delete(doc: fitz.Document, page_no: int, annot_id: int) -> dict:
    page = doc[page_no]
    page.delete_annot(_find(page, annot_id))
    return {"ok": True}


def flatten(doc: fitz.Document, annots: bool = True, widgets: bool = False) -> dict:
    """Bake annotations into page content so they can no longer be edited."""
    if not hasattr(doc, "bake"):
        raise PdfError("This build of PyMuPDF cannot flatten annotations.")
    doc.bake(annots=annots, widgets=widgets)
    return {"ok": True}


# ---- hyperlinks ----------------------------------------------------------

def links(doc: fitz.Document, page_no: int) -> list[dict]:
    page = doc[page_no]
    out = []
    for index, link in enumerate(page.get_links()):
        rect = fitz.Rect(link["from"])
        out.append({
            "index": index,
            "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
            "uri": link.get("uri", ""),
            "page": link.get("page", -1),
            "kind": "web" if link.get("uri") else "page",
        })
    return out


def add_link(doc: fitz.Document, page_no: int, rect: list[float],
             uri: str = "", target_page: int | None = None) -> dict:
    page = doc[page_no]
    box = fitz.Rect(rect)
    if uri:
        page.insert_link({"kind": fitz.LINK_URI, "from": box, "uri": uri})
    elif target_page is not None and 0 <= target_page < doc.page_count:
        page.insert_link({"kind": fitz.LINK_GOTO, "from": box,
                          "page": target_page, "to": fitz.Point(0, 0)})
    else:
        raise PdfError("A link needs either a URL or a target page.")
    return {"ok": True}


def delete_link(doc: fitz.Document, page_no: int, index: int) -> dict:
    page = doc[page_no]
    found = page.get_links()
    if index < 0 or index >= len(found):
        raise PdfError("That link no longer exists.")
    page.delete_link(found[index])
    return {"ok": True}
