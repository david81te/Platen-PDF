"""Interactive form fields: read, fill, create, export and flatten."""
from __future__ import annotations

import csv

import pymupdf as fitz

from .session import PdfError

KIND_NAMES = {
    fitz.PDF_WIDGET_TYPE_TEXT: "text",
    fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    fitz.PDF_WIDGET_TYPE_COMBOBOX: "dropdown",
    fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
    fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    fitz.PDF_WIDGET_TYPE_BUTTON: "button",
}

NEW_KINDS = {
    "text": fitz.PDF_WIDGET_TYPE_TEXT,
    "checkbox": fitz.PDF_WIDGET_TYPE_CHECKBOX,
    "dropdown": fitz.PDF_WIDGET_TYPE_COMBOBOX,
    "listbox": fitz.PDF_WIDGET_TYPE_LISTBOX,
}


def _describe(widget, page_no: int) -> dict:
    rect = widget.rect
    return {
        "page": page_no,
        "name": widget.field_name or "",
        "label": widget.field_label or "",
        "kind": KIND_NAMES.get(widget.field_type, "other"),
        "value": widget.field_value if widget.field_value is not None else "",
        "options": list(widget.choice_values or []),
        "readonly": bool(widget.field_flags & 1),
        "required": bool(widget.field_flags & 2),
        "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        "xref": widget.xref,
    }


def listing(doc: fitz.Document, page_no: int | None = None) -> list[dict]:
    pages = [page_no] if page_no is not None else range(doc.page_count)
    out = []
    for index in pages:
        for widget in doc[index].widgets():
            out.append(_describe(widget, index))
    return out


def _find(page: fitz.Page, name: str):
    """Look a widget up on a live Page.

    The caller must keep the page alive: a widget whose Page has been garbage
    collected is no longer bound to it and update() then fails.
    """
    for widget in page.widgets():
        if (widget.field_name or "") == name:
            return widget
    raise PdfError("Form field not found: " + str(name))


def set_value(doc: fitz.Document, page_no: int, name: str, value) -> dict:
    page = doc[page_no]
    widget = _find(page, name)
    if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        widget.field_value = bool(value)
    elif widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,
                               fitz.PDF_WIDGET_TYPE_LISTBOX):
        options = list(widget.choice_values or [])
        if value not in options:
            raise PdfError("Value must be one of: " + ", ".join(options))
        widget.field_value = value
    else:
        widget.field_value = "" if value is None else str(value)
    widget.update()
    return _describe(widget, page_no)


def set_many(doc: fitz.Document, values: list[dict]) -> dict:
    changed = 0
    for item in values:
        set_value(doc, int(item["page"]), item["name"], item.get("value"))
        changed += 1
    return {"updated": changed}


def add_field(doc: fitz.Document, page_no: int, kind: str, rect: list[float],
              name: str, value: str = "", options: list[str] | None = None,
              size: float = 11, required: bool = False) -> dict:
    if kind not in NEW_KINDS:
        raise PdfError("Cannot create a field of type: " + str(kind))
    if not name.strip():
        raise PdfError("Give the field a name.")
    page = doc[page_no]
    widget = fitz.Widget()
    widget.field_type = NEW_KINDS[kind]
    widget.field_name = name.strip()
    widget.rect = fitz.Rect(rect)
    widget.text_fontsize = size
    widget.border_color = (0.55, 0.6, 0.7)
    widget.border_width = 0.75
    widget.fill_color = (0.96, 0.975, 1.0)
    if kind in ("dropdown", "listbox"):
        widget.choice_values = list(options or [])
        widget.field_value = value or (options[0] if options else "")
    elif kind == "checkbox":
        widget.field_value = bool(value)
    else:
        widget.field_value = value or ""
    if required:
        widget.field_flags = widget.field_flags | 2
    page.add_widget(widget)
    return {"name": widget.field_name, "kind": kind}


def delete_field(doc: fitz.Document, page_no: int, name: str) -> dict:
    page = doc[page_no]
    widget = _find(page, name)
    page.delete_widget(widget)
    return {"ok": True}


def export_csv(doc: fitz.Document, path: str) -> dict:
    rows = listing(doc)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["page", "field", "type", "value"])
        for row in rows:
            writer.writerow([row["page"] + 1, row["name"], row["kind"], row["value"]])
    return {"path": path, "fields": len(rows)}


def import_csv(doc: fitz.Document, path: str) -> dict:
    applied = 0
    missing = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                page_no = int(row.get("page", 1)) - 1
            except (TypeError, ValueError):
                continue
            name = (row.get("field") or "").strip()
            if not name:
                continue
            try:
                set_value(doc, page_no, name, row.get("value", ""))
                applied += 1
            except PdfError:
                missing.append(name)
    return {"updated": applied, "missing": missing}


def flatten(doc: fitz.Document) -> dict:
    """Convert filled fields into permanent page content."""
    if not hasattr(doc, "bake"):
        raise PdfError("This build of PyMuPDF cannot flatten form fields.")
    doc.bake(annots=False, widgets=True)
    return {"ok": True}
