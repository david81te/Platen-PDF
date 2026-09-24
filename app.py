"""PDF Studio - a standalone PDF editor."""
from __future__ import annotations

import os
import sys

import webview

from backend.api import Api

APP_NAME = "PDF Studio"


def _base_dir() -> str:
    # PyInstaller unpacks bundled data into _MEIPASS at runtime.
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def selftest() -> int:
    """Headless check that a packaged build has everything it needs.

    Mainly proves the OCR models resolved inside the frozen bundle, which is
    the part most likely to break when PyInstaller relocates package data.
    """
    import pymupdf as fitz
    from backend import convert

    print("base dir:", _base_dir())
    print("ui present:", os.path.isfile(os.path.join(_base_dir(), "ui", "index.html")))
    status = convert.ocr_status()
    print("ocr status:", status)
    if not status.get("available"):
        print("SELFTEST: FAIL (no OCR engine)")
        return 1

    doc = fitz.open()
    page = doc.new_page(width=420, height=160)
    page.insert_text(fitz.Point(30, 90), "Willmore Capital 2026", fontsize=28)
    flat = fitz.open()
    image = flat.new_page(width=420, height=160)
    image.insert_image(image.rect, pixmap=page.get_pixmap(matrix=fitz.Matrix(3, 3)))
    print("text before ocr:", repr(flat[0].get_text().strip()))

    convert.ocr(flat)
    found = flat[0].get_text()
    print("text after ocr :", repr(found.strip()))
    ok = "Willmore" in found
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())

    api = Api()
    index = os.path.join(_base_dir(), "ui", "index.html")
    window = webview.create_window(
        APP_NAME,
        url=index,
        js_api=api,
        width=1480,
        height=940,
        min_size=(1060, 680),
        text_select=False,
    )
    api.attach_window(window)

    startup = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if startup:
        api._startup_path = os.path.abspath(startup[0])

    webview.start(debug=bool(os.environ.get("PDFSTUDIO_DEBUG")))


if __name__ == "__main__":
    main()
