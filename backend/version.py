"""One place for the name, version and copyright.

assets/version_info.txt carries the same strings for the Windows file
properties, because PyInstaller reads that at build time and cannot import
from here. Change the two together - tests/test_about.py fails if they drift.
"""
from __future__ import annotations

APP_NAME = "Platen PDF"
VERSION = "1.2.0"
AUTHOR = "David Willmore"
COPYRIGHT = "Copyright (c) 2026 David Willmore"
# AGPL section 5 wants an interactive program to say this where a user can
# see it; Help > About is that place, and it carries the source link too.
LICENCE = "Free software under the GNU Affero General Public License v3"
SOURCE = "https://github.com/david81te/Platen-PDF"

# Shown in Help > About. Only the components a user would recognise or that
# carry conditions; THIRD-PARTY.md has the full list.
COMPONENTS = [
    ("PyMuPDF", "AGPL-3.0 or Artifex commercial"),
    ("pikepdf", "MPL-2.0"),
    ("Pillow", "MIT-CMU"),
    ("RapidOCR + ONNX Runtime", "Apache-2.0 / MIT"),
    ("pdf2docx, python-docx, python-pptx, openpyxl", "MIT"),
    ("pywebview", "BSD"),
]


def about() -> dict:
    return {
        "name": APP_NAME,
        "version": VERSION,
        "author": AUTHOR,
        "copyright": COPYRIGHT,
        "licence": LICENCE,
        "source": SOURCE,
        "components": [{"name": n, "licence": l} for n, l in COMPONENTS],
    }
