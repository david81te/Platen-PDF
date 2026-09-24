"""Signature library and permanent signature placement.

Signatures are stored per person as PNGs in the user's app-data folder. Placing
one draws it straight into the page content stream rather than adding an
annotation, so once the file is saved the signature is part of the page and
cannot be selected, moved or deleted by a PDF reader.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import uuid

import pymupdf as fitz
from PIL import Image

from .session import PdfError

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                       "PDFEditorPro")
SIG_DIR = os.path.join(APP_DIR, "signatures")
INDEX = os.path.join(SIG_DIR, "index.json")


def _ensure() -> None:
    os.makedirs(SIG_DIR, exist_ok=True)


def _load() -> list[dict]:
    _ensure()
    if not os.path.isfile(INDEX):
        return []
    try:
        with open(INDEX, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _store(items: list[dict]) -> None:
    _ensure()
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2)


def _decode(source: str) -> bytes:
    if source.startswith("data:"):
        _, _, payload = source.partition(",")
        return base64.b64decode(payload)
    with open(source, "rb") as fh:
        return fh.read()


MAX_STORED_EDGE = 1600


def _clean(raw: bytes, drop_background: bool = True, threshold: int = 238) -> bytes:
    """Trim surrounding whitespace and knock out the paper background.

    Signatures are usually scanned or photographed on white paper. Left as-is
    they stamp an opaque white rectangle over the document, so near-white
    pixels become transparent and the image is cropped to the ink.

    The masking is vectorised: a phone photo is around 12 megapixels, and
    walking those pixels in Python froze the window for seconds.
    """
    import numpy as np

    image = Image.open(io.BytesIO(raw))
    image.draft("RGB", (MAX_STORED_EDGE * 2, MAX_STORED_EDGE * 2))  # cheap JPEG downscale
    image = image.convert("RGBA")

    if drop_background:
        pixels = np.array(image)
        near_white = (
            (pixels[:, :, 0] >= threshold)
            & (pixels[:, :, 1] >= threshold)
            & (pixels[:, :, 2] >= threshold)
        )
        pixels[near_white, 3] = 0
        image = Image.fromarray(pixels, "RGBA")

    box = image.getbbox()
    if box:
        image = image.crop(box)

    # A signature does not need to be a full-resolution photograph, and an
    # oversized one bloats every PDF it is stamped into.
    if max(image.size) > MAX_STORED_EDGE:
        ratio = MAX_STORED_EDGE / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)),
                              max(1, int(image.height * ratio))), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def listing() -> list[dict]:
    items = []
    for item in _load():
        path = os.path.join(SIG_DIR, item["file"])
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode()
        with Image.open(path) as image:
            width, height = image.size
        items.append({**item, "width": width, "height": height,
                      "ratio": (width / height) if height else 3.0,
                      "image": "data:image/png;base64," + encoded})
    return items


def add(source: str, name: str, role: str = "", drop_background: bool = True) -> dict:
    if not name.strip():
        raise PdfError("Give the signature a name.")
    try:
        data = _clean(_decode(source), drop_background)
    except Exception as exc:
        raise PdfError("Could not read that image: " + str(exc)) from exc
    _ensure()
    identifier = uuid.uuid4().hex[:12]
    filename = identifier + ".png"
    with open(os.path.join(SIG_DIR, filename), "wb") as fh:
        fh.write(data)
    items = _load()
    entry = {"id": identifier, "name": name.strip(), "role": role.strip(),
             "file": filename, "added": time.strftime("%Y-%m-%d %H:%M")}
    items.append(entry)
    _store(items)
    return entry


def rename(identifier: str, name: str, role: str = "") -> dict:
    items = _load()
    for item in items:
        if item["id"] == identifier:
            item["name"] = name.strip() or item["name"]
            item["role"] = role.strip()
            _store(items)
            return item
    raise PdfError("Signature not found.")


def remove(identifier: str) -> dict:
    items = _load()
    keep = [i for i in items if i["id"] != identifier]
    if len(keep) == len(items):
        raise PdfError("Signature not found.")
    for item in items:
        if item["id"] == identifier:
            try:
                os.remove(os.path.join(SIG_DIR, item["file"]))
            except OSError:
                pass
    _store(keep)
    return {"ok": True}


def _path_for(identifier: str) -> str:
    for item in _load():
        if item["id"] == identifier:
            path = os.path.join(SIG_DIR, item["file"])
            if os.path.isfile(path):
                return path
    raise PdfError("Signature not found.")


def place(doc: fitz.Document, page_no: int, identifier: str, rect: list[float],
          keep_ratio: bool = True) -> dict:
    """Stamp a stored signature permanently into the page content stream."""
    path = _path_for(identifier)
    page = doc[page_no]
    box = fitz.Rect(rect)
    if box.is_empty or box.width < 4 or box.height < 4:
        raise PdfError("Signature area is too small.")
    page.insert_image(box, filename=path, keep_proportion=keep_ratio, overlay=True)
    return {"ok": True, "rect": [box.x0, box.y0, box.x1, box.y1]}


def place_dated(doc: fitz.Document, page_no: int, identifier: str, rect: list[float],
                date_text: str = "", date_rect: list[float] | None = None,
                size: float = 10) -> dict:
    """Place a signature and, optionally, a typed date beside it."""
    result = place(doc, page_no, identifier, rect)
    if date_text:
        from . import textedit
        target = date_rect or [rect[2] + 8, rect[1], rect[2] + 160, rect[3]]
        textedit.add_text(doc, page_no, target, date_text, size=size)
    return result
