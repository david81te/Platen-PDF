"""Edited text must actually appear on the page, not just in the text layer.

The reported bug was letters and numbers going missing after an edit. Checking
the extracted text is not enough: glyphs drawn past the page edge are invisible
yet can still extract. These checks OCR the rendered page, so they see what a
reader sees.
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pymupdf as fitz
from rapidocr_onnxruntime import RapidOCR

from backend import textedit
from backend.session import Session

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
SRC = os.path.join(OUT, "fixture.pdf")
ENGINE = RapidOCR()
findings = []


def squash(text):
    return re.sub(r"[^0-9A-Za-z]", "", text).lower()


def read_page(doc, page_no=0):
    pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    found, _ = ENGINE(array)
    return squash(" ".join(t for _, t, _ in (found or [])))


def within_page(doc, page_no=0):
    """True when no glyph sits outside the visible page area."""
    page = doc[page_no]
    edge = page.rect
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        box = fitz.Rect(block["bbox"])
        if box.x1 > edge.x1 + 1 or box.x0 < edge.x0 - 1:
            return False
        if box.y1 > edge.y1 + 1 or box.y0 < edge.y0 - 1:
            return False
    return True


def run_case(label, replacement, needle="4,250,000"):
    session = Session()
    session.open(SRC)
    span = next(s for b in textedit.layout(session.doc[0])["blocks"]
                for ln in b["lines"] for s in ln["spans"] if needle in s["text"])
    result = textedit.edit_span(session.doc, span["id"], replacement)
    session.reload()
    target = os.path.join(OUT, "render_%s.pdf" % squash(label)[:16])
    session.save(target)
    session.close()

    doc = fitz.open(target)
    flat = " ".join(doc[0].get_text().split())
    on_screen = read_page(doc)
    inside = within_page(doc)
    doc.close()

    words = [w for w in re.findall(r"[0-9A-Za-z]{3,}", replacement)]
    missing = [w for w in words if squash(w) not in on_screen]
    # Also confirm the untouched parts of the document survived.
    kept = "Deposit" in flat and "Mar 31" in flat

    ok = not missing and inside and kept
    if not ok:
        findings.append((label, "missing=%s inside=%s kept=%s" % (missing, inside, kept)))
    print("  %s %-26s missing=%-28s inside_page=%s"
          % ("ok  " if ok else "BUG ", label, missing or "none", inside))
    return result


print("== edited text must be visible ==")
run_case("same length", "$9,875,400")
run_case("a little longer", "$9,875,400 (nine million)")
run_case("much longer", "The purchase price is $4,250,000 payable in immediately "
                        "available funds at closing on the Closing Date as defined in "
                        "Section 2.1 hereof and the balance within thirty days "
                        "thereafter without setoff or deduction 1234567890")
run_case("shorter", "$1")
run_case("digits only", "1234567890 0987654321")
run_case("punctuation heavy", "Ref #A-12/B (rev. 3) @ 50% -- see note [4]")
run_case("accented", "Señor François Müller café €50")
run_case("in a bold run", "REVISED 2026 TERMS", needle="proposed terms")

print("\n== paragraph rewrite must be visible ==")


def block_case(label, text):
    session = Session()
    session.open(SRC)
    block = max(textedit.layout(session.doc[0])["blocks"], key=lambda b: len(b["text"]))
    textedit.edit_block(session.doc, block["id"], text)
    session.reload()
    target = os.path.join(OUT, "renderblk_%s.pdf" % squash(label)[:14])
    session.save(target)
    session.close()
    doc = fitz.open(target)
    on_screen = read_page(doc)
    inside = within_page(doc)
    doc.close()
    missing = [w for w in re.findall(r"[0-9A-Za-z]{3,}", text)
               if squash(w) not in on_screen]
    ok = not missing and inside
    if not ok:
        findings.append((label, "missing=%s inside=%s" % (missing, inside)))
    print("  %s %-26s missing=%-28s inside_page=%s"
          % ("ok  " if ok else "BUG ", label, missing or "none", inside))


block_case("short paragraph", "Price revised to $9,875,400 payable at closing.")
block_case("long paragraph",
           "The purchase price has been revised upward to $9,875,400 following the "
           "second diligence review and is now payable in three tranches of "
           "$3,291,800 each on 15 January, 15 February and 31 March 2026, in each "
           "case without setoff, deduction or counterclaim of any kind whatsoever.")

print("\n" + "=" * 62)
print("No issues found." if not findings
      else "%d issue(s):\n  %s" % (len(findings), findings))
sys.exit(1 if findings else 0)
