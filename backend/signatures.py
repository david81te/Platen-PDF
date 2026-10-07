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

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")

# PLATENPDF_DATA_DIR redirects the whole signature library somewhere else.
# It exists for the tests: without it they add, rename and delete signatures
# in the real library belonging to whoever is running them, and a test that
# dies before its cleanup leaves its litter in a person's saved signatures.
APP_DIR = os.environ.get("PLATENPDF_DATA_DIR") or os.path.join(_APPDATA, "PlatenPDF")

# Where signatures lived when the program was called PDF Studio. Anyone who
# used it before the rename still has their signatures here.
LEGACY_DIR = os.path.join(_APPDATA, "PDFEditorPro")
SIG_DIR = os.path.join(APP_DIR, "signatures")
INDEX = os.path.join(SIG_DIR, "index.json")


def _migrate_legacy() -> None:
    """Carry a pre-rename signature library over, once.

    Only runs when the new folder does not exist yet, so it can never
    overwrite signatures saved under the new name.
    """
    if os.environ.get("PLATENPDF_DATA_DIR"):
        return      # a redirected library has nothing to inherit
    if os.path.exists(APP_DIR) or not os.path.isdir(LEGACY_DIR):
        return
    try:
        os.rename(LEGACY_DIR, APP_DIR)
    except OSError:
        # A locked file or a cross-volume profile: fall back to copying, and
        # leave the old folder alone rather than risk losing the only copy.
        import shutil
        try:
            shutil.copytree(LEGACY_DIR, APP_DIR)
        except OSError:
            pass


def _ensure() -> None:
    _migrate_legacy()
    os.makedirs(SIG_DIR, exist_ok=True)


def _stamp() -> str:
    """UTC, with microseconds.

    Sync compares these across machines, so local time would make the newer
    copy lose whenever the clocks disagreed. The fraction matters as much as
    the zone: truncated to whole seconds, two edits in the same second compare
    equal and the later one is silently discarded - which is exactly what
    happened, and only showed up once the tests ran fast enough to do both
    inside one second.
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# Deleting a signature has to be something the other device can learn about.
# Simply dropping the row would let the next sync see it missing locally,
# decide it is new over there, and download it straight back.
TOMBSTONES = os.path.join(APP_DIR, "deleted-signatures.json")


def tombstones() -> list[dict]:
    try:
        with open(TOMBSTONES, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _remember_deletion(entry: dict) -> None:
    if not entry.get("remote_id"):
        return          # never synced, so there is nothing out there to remove
    marks = tombstones()
    marks.append({"remote_id": entry["remote_id"], "at": _stamp()})
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(TOMBSTONES, "w", encoding="utf-8") as fh:
            json.dump(marks[-500:], fh, indent=1)
    except OSError:
        pass


def forget_tombstone(remote_id: str) -> None:
    """Called once the deletion has reached the server."""
    marks = [m for m in tombstones() if m.get("remote_id") != remote_id]
    try:
        with open(TOMBSTONES, "w", encoding="utf-8") as fh:
            json.dump(marks, fh, indent=1)
    except OSError:
        pass


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
             "file": filename, "added": time.strftime("%Y-%m-%d %H:%M"),
             "updated": _stamp(), "remote_id": None}
    items.append(entry)
    _store(items)
    return entry


# Handwriting faces that ship with Windows. Each is checked for before it is
# offered, because a missing font would otherwise render as a default sans and
# produce a "signature" that looks like a label.
SIGNATURE_FONTS = [
    ("Segoe Script", "segoesc.ttf"),
    ("Brush Script", "BRUSHSCI.TTF"),
    ("Lucida Handwriting", "LHANDW.TTF"),
    ("Edwardian Script", "ITCEDSCR.TTF"),
    ("Freestyle Script", "FREESCPT.TTF"),
    ("Kunstler Script", "KUNSTLER.TTF"),
    ("Mistral", "MISTRAL.TTF"),
    ("Vladimir Script", "VLADIMIR.TTF"),
    ("Rage Italic", "RAGE.TTF"),
    ("Ink Free", "Inkfree.ttf"),
    ("Segoe Print", "segoepr.ttf"),
    ("Palace Script", "PALSCRI.TTF"),
]
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
INK = (16, 24, 92)          # a dark blue-black, the way a pen looks on paper


def fonts() -> list[dict]:
    """The handwriting faces actually present on this machine."""
    out = []
    for label, filename in SIGNATURE_FONTS:
        if os.path.isfile(os.path.join(FONT_DIR, filename)):
            out.append({"id": filename, "label": label})
    return out


def render_typed(text: str, font_id: str, height: int = 180) -> bytes:
    """Draw typed text as a signature image, cropped to the ink.

    Returns a transparent PNG. Nothing is stored; this is what the dialog
    shows while someone is still choosing a face.
    """
    from PIL import Image, ImageDraw, ImageFont

    text = (text or "").strip()
    if not text:
        raise PdfError("Type a name first.")

    available = {f["id"] for f in fonts()}
    if font_id not in available:
        chosen = next(iter(available), None)
        if not chosen:
            raise PdfError("No handwriting fonts are installed on this PC.")
        font_id = chosen

    try:
        face = ImageFont.truetype(os.path.join(FONT_DIR, font_id), height)
    except OSError as exc:
        raise PdfError("Could not load that handwriting style.") from exc

    # Draw oversized on a transparent canvas, then crop to what was actually
    # drawn. Script faces have wildly different metrics, so trusting the
    # reported size leaves some signatures swimming in space and others cut.
    pad = height
    canvas = Image.new("RGBA", (pad * 2 + height * len(text), height * 3), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, pad // 2), text, font=face, fill=INK + (255,))
    box = canvas.getbbox()
    if not box:
        raise PdfError("That came out blank. Try another handwriting style.")
    margin = max(4, height // 14)
    box = (max(0, box[0] - margin), max(0, box[1] - margin),
           min(canvas.width, box[2] + margin), min(canvas.height, box[3] + margin))
    trimmed = canvas.crop(box)

    buffer = io.BytesIO()
    trimmed.save(buffer, format="PNG")
    return buffer.getvalue()


def add_typed(text: str, font_id: str, name: str = "", role: str = "") -> dict:
    """Save a typed signature into the library."""
    data = render_typed(text, font_id)
    # drop_background is off: the image is already transparent, and the
    # whitening pass would eat the anti-aliased edges of the strokes.
    return add("data:image/png;base64," + base64.b64encode(data).decode(),
               (name or text).strip(), role, drop_background=False)


def rename(identifier: str, name: str, role: str = "") -> dict:
    items = _load()
    for item in items:
        if item["id"] == identifier:
            item["name"] = name.strip() or item["name"]
            item["role"] = role.strip()
            item["updated"] = _stamp()
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
            _remember_deletion(item)
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


def _image_xref(doc: fitz.Document, path: str) -> tuple[int, int, int]:
    """Add an image XObject to the file without drawing it on any page.

    Returns (xref, width, height). Transparency is carried in a separate
    /SMask image, which is how PDF represents an alpha channel -- without it a
    signature would stamp an opaque white rectangle over the document.
    """
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    width, height = image.size
    rgb = image.convert("RGB").tobytes()
    alpha = image.getchannel("A").tobytes()

    smask = doc.get_new_xref()
    doc.update_object(smask, "<</Type/XObject/Subtype/Image/Width %d/Height %d"
                             "/ColorSpace/DeviceGray/BitsPerComponent 8>>"
                             % (width, height))
    doc.update_stream(smask, alpha, compress=True)

    xref = doc.get_new_xref()
    doc.update_object(xref, "<</Type/XObject/Subtype/Image/Width %d/Height %d"
                            "/ColorSpace/DeviceRGB/BitsPerComponent 8/SMask %d 0 R>>"
                            % (width, height, smask))
    doc.update_stream(xref, rgb, compress=True)
    return xref, width, height


def place(doc: fitz.Document, page_no: int, identifier: str, rect: list[float],
          keep_ratio: bool = True, flatten: bool = False) -> dict:
    """Put a stored signature on the page.

    By default this is a stamp annotation carrying the signature image, so it
    can still be moved, resized or removed. Flattening it -- on its own, or
    with every other annotation -- draws it into the page content, after which
    it is part of the page and cannot be selected again.
    """
    path = _path_for(identifier)
    page = doc[page_no]
    box = fitz.Rect(rect)
    if box.is_empty or box.width < 4 or box.height < 4:
        raise PdfError("Signature area is too small.")

    if flatten:
        page.insert_image(box, filename=path, keep_proportion=keep_ratio, overlay=True)
        return {"ok": True, "flattened": True,
                "rect": [box.x0, box.y0, box.x1, box.y1]}

    xref, width, height = _image_xref(doc, path)
    if keep_ratio and width and height:
        scale = min(box.width / width, box.height / height)
        box = fitz.Rect(box.x0, box.y0, box.x0 + width * scale, box.y0 + height * scale)

    annot = page.add_stamp_annot(box, stamp=0)
    name = next((i["name"] for i in _load() if i["id"] == identifier), "Signature")
    annot.set_info(title=name, content="Signature")
    annot.update()

    # Swap the built-in stamp artwork for the signature image.
    kind, value = doc.xref_get_key(annot.xref, "AP/N")
    if kind != "xref":
        raise PdfError("Could not build the signature appearance.")
    appearance = int(value.split()[0])
    doc.update_stream(appearance,
                      ("q %d 0 0 %d 0 0 cm /SigIm Do Q" % (width, height)).encode())
    doc.xref_set_key(appearance, "Resources/XObject/SigIm", "%d 0 R" % xref)
    doc.xref_set_key(appearance, "BBox", "[0 0 %d %d]" % (width, height))

    # add_stamp_annot re-fits the rect to the built-in artwork it started from,
    # so restore the caller's box now that our own appearance is in place.
    annot.set_rect(box)

    return {"ok": True, "flattened": False, "id": annot.xref,
            "rect": [box.x0, box.y0, box.x1, box.y1]}


def place_dated(doc: fitz.Document, page_no: int, identifier: str, rect: list[float],
                date_text: str = "", date_rect: list[float] | None = None,
                size: float = 10, flatten: bool = False) -> dict:
    """Place a signature and, optionally, a typed date beside it."""
    result = place(doc, page_no, identifier, rect, flatten=flatten)
    if date_text:
        from . import textedit
        target = date_rect or [rect[2] + 8, rect[1], rect[2] + 160, rect[3]]
        textedit.add_text(doc, page_no, target, date_text, size=size)
    return result
