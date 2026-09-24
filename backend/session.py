"""Open document state: undo/redo, rendering, search, bookmarks."""
from __future__ import annotations

import base64
import os

import pymupdf as fitz

MAX_UNDO = 25


class NoDocument(Exception):
    pass


class PdfError(Exception):
    pass


class Session:
    def __init__(self):
        self.doc: fitz.Document | None = None
        self.path: str | None = None
        self.dirty = False
        self.open_password: str | None = None
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []

    # ---- lifecycle -----------------------------------------------------

    def require(self) -> fitz.Document:
        if self.doc is None:
            raise NoDocument("No document is open.")
        return self.doc

    def open(self, path: str, password: str | None = None) -> dict:
        doc = fitz.open(path)
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                doc.close()
                raise PdfError("PASSWORD_REQUIRED")
        self.close()
        self.doc = doc
        self.path = path
        self.open_password = password
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        return self.info()

    def open_bytes(self, data: bytes, label: str = "Untitled.pdf") -> dict:
        doc = fitz.open(stream=data, filetype="pdf")
        self.close()
        self.doc = doc
        self.path = None
        self.dirty = True
        self._undo.clear()
        self._redo.clear()
        return self.info()

    def adopt(self, doc: fitz.Document, path: str | None = None, dirty: bool = True) -> dict:
        """Replace the open document with an already-built one."""
        self.close()
        self.doc = doc
        self.path = path
        self.dirty = dirty
        self._undo.clear()
        self._redo.clear()
        return self.info()

    def close(self):
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.path = None
        self.dirty = False
        self._undo.clear()
        self._redo.clear()

    def info(self) -> dict:
        if self.doc is None:
            return {"open": False}
        meta = self.doc.metadata or {}
        return {
            "open": True,
            "path": self.path,
            "name": os.path.basename(self.path) if self.path else "Untitled.pdf",
            "page_count": self.doc.page_count,
            "dirty": self.dirty,
            "encrypted": bool(self.doc.needs_pass) or self.doc.is_encrypted,
            "title": meta.get("title") or "",
            "author": meta.get("author") or "",
            "subject": meta.get("subject") or "",
            "keywords": meta.get("keywords") or "",
            "can_undo": bool(self._undo),
            "can_redo": bool(self._redo),
            "has_form": self.doc.is_form_pdf,
        }

    def page(self, index: int) -> fitz.Page:
        doc = self.require()
        if index < 0 or index >= doc.page_count:
            raise PdfError(f"Page {index + 1} is out of range.")
        return doc[index]

    # ---- undo / redo ---------------------------------------------------

    def _snapshot(self) -> bytes:
        return self.require().tobytes(garbage=0, deflate=True)

    def checkpoint(self):
        """Record the current state so the next mutation can be undone."""
        try:
            self._undo.append(self._snapshot())
        except Exception:
            return
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def touch(self):
        self.dirty = True

    def _restore(self, data: bytes):
        doc = fitz.open(stream=data, filetype="pdf")
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = doc

    def reload(self):
        """Re-open the document from its own bytes.

        MuPDF caches a font's ToUnicode CMap on first use. Text editing embeds
        fonts and rewrites those CMaps, so without a reload the rest of the
        session keeps extracting text through the stale cache -- the file on
        disk is correct but search and export would see the old mapping.
        """
        if self.doc is None:
            return
        self._restore(self.doc.tobytes(garbage=0, deflate=True))

    def undo(self) -> dict:
        if not self._undo:
            raise PdfError("Nothing to undo.")
        current = self._snapshot()
        self._restore(self._undo.pop())
        self._redo.append(current)
        self.dirty = True
        return self.info()

    def redo(self) -> dict:
        if not self._redo:
            raise PdfError("Nothing to redo.")
        current = self._snapshot()
        self._restore(self._redo.pop())
        self._undo.append(current)
        self.dirty = True
        return self.info()

    # ---- saving --------------------------------------------------------

    def save(self, path: str | None = None) -> dict:
        doc = self.require()
        target = path or self.path
        if not target:
            raise PdfError("No destination path.")
        same_file = self.path is not None and os.path.abspath(target) == os.path.abspath(self.path)
        if same_file:
            # Saving over the open file requires a full rewrite via a temp copy.
            data = doc.tobytes(garbage=4, deflate=True)
            doc.close()
            with open(target, "wb") as fh:
                fh.write(data)
            self.doc = fitz.open(target)
        else:
            doc.save(target, garbage=4, deflate=True)
        self.path = target
        self.dirty = False
        return self.info()

    # ---- rendering -----------------------------------------------------

    def render(self, index: int, zoom: float = 1.0) -> dict:
        page = self.page(index)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        rect = page.rect
        return {
            "index": index,
            "zoom": zoom,
            "width": pix.width,
            "height": pix.height,
            "pdf_width": rect.width,
            "pdf_height": rect.height,
            "rotation": page.rotation,
            "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode(),
        }

    def thumbnails(self, start: int, count: int, width: int = 150) -> list[dict]:
        doc = self.require()
        out = []
        for index in range(start, min(start + count, doc.page_count)):
            page = doc[index]
            zoom = width / page.rect.width if page.rect.width else 1.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            out.append({
                "index": index,
                "width": pix.width,
                "height": pix.height,
                "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode(),
            })
        return out

    def page_sizes(self) -> list[dict]:
        doc = self.require()
        return [
            {"index": i, "width": doc[i].rect.width, "height": doc[i].rect.height,
             "rotation": doc[i].rotation}
            for i in range(doc.page_count)
        ]

    # ---- search & outline ----------------------------------------------

    def search(self, query: str, match_case: bool = False,
               limit: int = 800) -> dict:
        """Find every occurrence, with the line it sits on as context."""
        doc = self.require()
        query = query or ""
        if not query.strip():
            return {"query": query, "hits": [], "truncated": False}

        hits = []
        for index in range(doc.page_count):
            page = doc[index]
            for rect in page.search_for(query):
                if match_case:
                    # search_for ignores case, so confirm the real casing.
                    if query not in (page.get_textbox(rect) or ""):
                        continue
                line = fitz.Rect(page.rect.x0, rect.y0 - 1,
                                 page.rect.x1, rect.y1 + 1)
                snippet = " ".join((page.get_textbox(line) or "").split())
                hits.append({
                    "page": index,
                    "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                    "snippet": snippet[:160],
                })
                if len(hits) >= limit:
                    return {"query": query, "hits": hits, "truncated": True}
        return {"query": query, "hits": hits, "truncated": False}

    def outline(self) -> list[dict]:
        doc = self.require()
        return [
            {"level": level, "title": title, "page": max(0, page - 1)}
            for level, title, page in doc.get_toc(simple=True)
        ]

    def set_outline(self, items: list[dict]) -> dict:
        doc = self.require()
        self.checkpoint()
        toc = [[int(i["level"]), str(i["title"]), int(i["page"]) + 1] for i in items]
        doc.set_toc(toc)
        self.touch()
        return {"count": len(toc)}

    def set_metadata(self, fields: dict) -> dict:
        doc = self.require()
        self.checkpoint()
        meta = dict(doc.metadata or {})
        for key in ("title", "author", "subject", "keywords"):
            if key in fields:
                meta[key] = fields[key] or ""
        doc.set_metadata(meta)
        self.touch()
        return self.info()
