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


TRUTHY = {"yes", "true", "on", "1", "checked", "y", "x"}


def _as_bool(value) -> bool:
    """Coerce a checkbox value.

    CSV round trips hand back strings, and bool("Off") is True -- which would
    tick every box on import.
    """
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    return bool(value)


def on_state(widget) -> str | None:
    """The name this widget uses for "on", or None if it has none.

    A tick box is not on and off, it is Off and something - "Yes" usually, but
    a radio button uses the name of its own option, which is how three buttons
    sharing one field name stay apart. PyMuPDF raises rather than answering
    when a widget has no pressed-down appearance, which plenty of real tick
    boxes do not, so the question is asked here and nowhere else.
    """
    try:
        return widget.on_state()
    except Exception:
        return None


BUTTON_TYPES = (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON)


def is_on(widget, state: str | None = None) -> bool:
    """Whether this particular widget is the one that is ticked or chosen.

    Only a tick box or a radio button can be on at all. Asking it of a text
    field has to answer no, not "it has something in it" - a filled-in name
    is not a tick, and reporting it as one puts a tick on screen over a
    perfectly ordinary text box.
    """
    if widget.field_type not in BUTTON_TYPES:
        return False
    if state is None:
        state = on_state(widget)
    value = widget.field_value
    if isinstance(value, bool):
        return value
    text = "" if value is None else str(value)
    if not text or text.lower() == "off":
        return False
    # A radio group puts the chosen option's name in every kid, so a kid is
    # only the chosen one when the name is its own.
    return text == state if state else True


def _describe(widget, page_no: int) -> dict:
    rect = widget.rect
    state = on_state(widget)
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
        # Which of the group this one is, and whether it is the chosen one.
        # Without these a radio group is three identical-looking widgets with
        # the same name and no way to tell which the person clicked.
        "on_state": state,
        "checked": is_on(widget, state),
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


def _find_xref(page: fitz.Page, xref: int):
    """Look a widget up by the object it actually is.

    Needed because a radio group is several widgets sharing one field name:
    looked up by name, every button in the group is the first button, so
    choosing Blue used to record Red. The caller must keep the page alive.
    """
    for widget in page.widgets():
        if widget.xref == int(xref):
            return widget
    raise PdfError("That form field is no longer on this page.")


def _apply(widget, value) -> None:
    """Put a value into one widget, in the form that kind of widget takes."""
    if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
        widget.field_value = _as_bool(value)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        # A radio button is chosen by writing its own option name. Writing
        # anything else either does nothing or, worse, picks a different
        # button. Clearing one is deliberately not offered: a PDF radio group
        # does not let go once chosen, the same as on a paper form, and
        # pretending otherwise would show a cleared button that is still set
        # in the file.
        state = on_state(widget)
        if not state:
            raise PdfError("That button has no option name to set.")
        widget.field_value = state
    elif widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,
                               fitz.PDF_WIDGET_TYPE_LISTBOX):
        options = list(widget.choice_values or [])
        if value not in options:
            raise PdfError("Value must be one of: " + ", ".join(options))
        widget.field_value = value
    else:
        widget.field_value = "" if value is None else str(value)
    widget.update()


def set_widget(doc: fitz.Document, page_no: int, xref: int, value) -> dict:
    """Set one particular widget, named or not, by the object it is."""
    page = doc[page_no]
    widget = _find_xref(page, xref)
    if widget.field_flags & 1:
        raise PdfError("That field is read only.")
    _apply(widget, value)
    return _describe(widget, page_no)


def set_value(doc: fitz.Document, page_no: int, name: str, value) -> dict:
    page = doc[page_no]
    widget = _find(page, name)
    if widget.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        # By name there is a group rather than a button, so the value says
        # which button is meant. This is the road a CSV import comes in by.
        wanted = "" if value is None else str(value)
        for candidate in page.widgets():
            if ((candidate.field_name or "") == name
                    and candidate.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                    and on_state(candidate) == wanted):
                _apply(candidate, wanted)
                return _describe(candidate, page_no)
        choices = [on_state(c) for c in page.widgets()
                   if (c.field_name or "") == name
                   and c.field_type == fitz.PDF_WIDGET_TYPE_RADIOBUTTON]
        choices = [c for c in choices if c]
        raise PdfError("%r is not one of the options for %s: %s"
                       % (wanted, name, ", ".join(choices) or "none found"))
    _apply(widget, value)
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
        widget.field_value = _as_bool(value)
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
