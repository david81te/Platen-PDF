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


def main() -> None:
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
