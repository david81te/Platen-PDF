"""Passwords, permissions and true redaction."""
from __future__ import annotations

import pymupdf as fitz

from .session import PdfError

PERMISSIONS = {
    "print": fitz.PDF_PERM_PRINT,
    "modify": fitz.PDF_PERM_MODIFY,
    "copy": fitz.PDF_PERM_COPY,
    "annotate": fitz.PDF_PERM_ANNOTATE,
    "forms": fitz.PDF_PERM_FORM,
    "accessibility": fitz.PDF_PERM_ACCESSIBILITY,
    "assemble": fitz.PDF_PERM_ASSEMBLE,
    "print_hq": fitz.PDF_PERM_PRINT_HQ,
}

ALL_ALLOWED = 0
for _flag in PERMISSIONS.values():
    ALL_ALLOWED |= _flag


def permission_flags(allowed: list[str] | None) -> int:
    if allowed is None:
        return ALL_ALLOWED
    value = 0
    for name in allowed:
        flag = PERMISSIONS.get(name)
        if flag:
            value |= flag
    return value


def describe(doc: fitz.Document, encrypted: bool = False) -> dict:
    """Report permissions.

    `encrypted` is passed in rather than read from the document: probing
    needs_pass after a successful authenticate re-locks it.
    """
    granted = []
    for name, flag in PERMISSIONS.items():
        if doc.permissions & flag:
            granted.append(name)
    return {"encrypted": bool(encrypted), "allowed": granted}


def save_protected(doc: fitz.Document, path: str, user_password: str = "",
                   owner_password: str = "", allowed: list[str] | None = None) -> dict:
    """Write an encrypted copy.

    PDF encryption is applied when the file is written, so this saves to a new
    path rather than mutating the open document.
    """
    if not user_password and not owner_password:
        raise PdfError("Set an open password, a permissions password, or both.")
    doc.save(
        path,
        garbage=4,
        deflate=True,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw=owner_password or user_password,
        user_pw=user_password or "",
        permissions=permission_flags(allowed),
    )
    return {"path": path, "encrypted": True}


def save_unprotected(doc: fitz.Document, path: str) -> dict:
    """Write a decrypted copy of an already-unlocked document."""
    doc.save(path, garbage=4, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
    return {"path": path, "encrypted": False}


# ---- redaction -----------------------------------------------------------

def mark(doc: fitz.Document, page_no: int, rects: list[list[float]],
         label: str = "", fill: tuple = (0, 0, 0)) -> dict:
    """Queue areas for redaction; nothing is removed until apply() runs."""
    page = doc[page_no]
    for raw in rects:
        page.add_redact_annot(fitz.Rect(raw), text=label or None, fill=fill)
    return {"marked": len(rects)}


def mark_matches(doc: fitz.Document, needle: str, fill: tuple = (0, 0, 0)) -> dict:
    if not needle.strip():
        raise PdfError("Enter the text to redact.")
    total = 0
    for index in range(doc.page_count):
        page = doc[index]
        for rect in page.search_for(needle):
            page.add_redact_annot(rect, fill=fill)
            total += 1
    return {"marked": total}


def pending(doc: fitz.Document, page_no: int) -> int:
    count = 0
    for annot in doc[page_no].annots():
        if annot.type[0] == fitz.PDF_ANNOT_REDACT:
            count += 1
    return count


def apply(doc: fitz.Document, remove_images: bool = True) -> dict:
    """Permanently delete marked content.

    Unlike drawing a black box, this removes the underlying text and image
    data, so the redacted content cannot be recovered by copying the page
    text or extracting images.
    """
    touched = 0
    for index in range(doc.page_count):
        page = doc[index]
        if pending(doc, index) == 0:
            continue
        kwargs = {}
        image_mode = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", None)
        if not remove_images:
            image_mode = getattr(fitz, "PDF_REDACT_IMAGE_NONE", image_mode)
        if image_mode is not None:
            kwargs["images"] = image_mode
        page.apply_redactions(**kwargs)
        touched += 1
    if not touched:
        raise PdfError("Nothing is marked for redaction.")
    return {"pages": touched}


def clear_marks(doc: fitz.Document) -> dict:
    removed = 0
    for index in range(doc.page_count):
        page = doc[index]
        for annot in list(page.annots()):
            if annot.type[0] == fitz.PDF_ANNOT_REDACT:
                page.delete_annot(annot)
                removed += 1
    return {"cleared": removed}
