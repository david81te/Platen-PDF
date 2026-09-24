"""Page organisation: rotate, delete, insert, move, crop, extract, merge, split."""
from __future__ import annotations

import os

import pymupdf as fitz

from .session import PdfError

PAPER = {
    "letter": (612, 792),
    "legal": (612, 1008),
    "tabloid": (792, 1224),
    "a4": (595, 842),
    "a3": (842, 1191),
}


def _validate(doc: fitz.Document, indices: list[int]) -> list[int]:
    clean = sorted({int(i) for i in indices})
    if not clean:
        raise PdfError("No pages selected.")
    if clean[0] < 0 or clean[-1] >= doc.page_count:
        raise PdfError("Page selection is out of range.")
    return clean


def rotate(doc: fitz.Document, indices: list[int], degrees: int) -> dict:
    for index in _validate(doc, indices):
        page = doc[index]
        page.set_rotation((page.rotation + degrees) % 360)
    return {"rotated": len(indices)}


def delete(doc: fitz.Document, indices: list[int]) -> dict:
    clean = _validate(doc, indices)
    if len(clean) >= doc.page_count:
        raise PdfError("A document must keep at least one page.")
    doc.delete_pages(clean)
    return {"deleted": len(clean), "page_count": doc.page_count}


def insert_blank(doc: fitz.Document, at: int, paper: str = "letter",
                 landscape: bool = False) -> dict:
    width, height = PAPER.get(paper.lower(), PAPER["letter"])
    if landscape:
        width, height = height, width
    at = max(0, min(int(at), doc.page_count))
    doc.new_page(pno=at, width=width, height=height)
    return {"page_count": doc.page_count, "index": at}


def move(doc: fitz.Document, source: int, target: int) -> dict:
    """Move a page so it sits *before* what is currently at `target`.

    Because the page is removed before being reinserted, moving forwards lands
    it one index earlier than `target`: move(0, 3) on a 7-page document leaves
    that page at index 2. Use reorder() when you want exact final positions.
    """
    if source < 0 or source >= doc.page_count:
        raise PdfError("Page is out of range.")
    target = max(0, min(int(target), doc.page_count))
    doc.move_page(source, target)
    return {"page_count": doc.page_count}


def reorder(doc: fitz.Document, order: list[int]) -> dict:
    if sorted(order) != list(range(doc.page_count)):
        raise PdfError("New page order must list every page exactly once.")
    doc.select(list(order))
    return {"page_count": doc.page_count}


def duplicate(doc: fitz.Document, indices: list[int]) -> dict:
    for offset, index in enumerate(_validate(doc, indices)):
        source = index + offset
        target = source + 1
        doc.fullcopy_page(source, -1 if target >= doc.page_count else target)
    return {"page_count": doc.page_count}


def crop(doc: fitz.Document, index: int, rect: list[float]) -> dict:
    page = doc[index]
    box = fitz.Rect(rect) & page.rect
    if box.is_empty or box.width < 10 or box.height < 10:
        raise PdfError("Crop area is too small.")
    page.set_cropbox(box)
    return {"rect": [box.x0, box.y0, box.x1, box.y1]}


def reset_crop(doc: fitz.Document, index: int) -> dict:
    page = doc[index]
    page.set_cropbox(page.mediabox)
    return {"ok": True}


def extract(doc: fitz.Document, indices: list[int], out_path: str) -> dict:
    clean = _validate(doc, indices)
    out = fitz.open()
    out.insert_pdf(doc, from_page=0, to_page=doc.page_count - 1)
    out.select(clean)
    out.save(out_path, garbage=4, deflate=True)
    out.close()
    return {"path": out_path, "pages": len(clean)}


def merge(doc: fitz.Document, paths: list[str], at: int | None = None) -> dict:
    added = 0
    position = doc.page_count if at is None else max(0, min(int(at), doc.page_count))
    for path in paths:
        if not os.path.isfile(path):
            raise PdfError("File not found: " + os.path.basename(path))
        other = fitz.open(path)
        if other.needs_pass:
            other.close()
            raise PdfError(os.path.basename(path) + " is password protected.")
        if not other.is_pdf:
            converted = fitz.open("pdf", other.convert_to_pdf())
            other.close()
            other = converted
        doc.insert_pdf(other, start_at=position)
        position += other.page_count
        added += other.page_count
        other.close()
    return {"added": added, "page_count": doc.page_count}


def merge_document(doc: fitz.Document, other: fitz.Document,
                   at: int | None = None) -> dict:
    """Merge an already-open document (another tab) into this one."""
    if other is doc:
        raise PdfError("A document cannot be merged into itself.")
    position = doc.page_count if at is None else max(0, min(int(at), doc.page_count))
    doc.insert_pdf(other, start_at=position)
    return {"added": other.page_count, "page_count": doc.page_count}


def split(doc: fitz.Document, out_dir: str, mode: str = "every",
          size: int = 1, ranges: str = "", stem: str = "part") -> dict:
    os.makedirs(out_dir, exist_ok=True)
    groups: list[list[int]] = []
    if mode == "ranges":
        for chunk in ranges.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                first, last = chunk.split("-", 1)
                start, end = int(first) - 1, int(last) - 1
            else:
                start = end = int(chunk) - 1
            if start < 0 or end >= doc.page_count or start > end:
                raise PdfError("Invalid page range: " + chunk)
            groups.append(list(range(start, end + 1)))
    else:
        step = max(1, int(size))
        groups = [list(range(i, min(i + step, doc.page_count)))
                  for i in range(0, doc.page_count, step)]
    if not groups:
        raise PdfError("Nothing to split.")

    written = []
    for number, group in enumerate(groups, start=1):
        out = fitz.open()
        out.insert_pdf(doc, from_page=0, to_page=doc.page_count - 1)
        out.select(group)
        path = os.path.join(out_dir, "%s_%02d.pdf" % (stem, number))
        out.save(path, garbage=4, deflate=True)
        out.close()
        written.append(path)
    return {"files": written, "count": len(written)}
