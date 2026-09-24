"""In-place text editing against a real Word-exported PDF."""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf as fitz
from backend import textedit

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
SRC = os.path.join(OUT, "fixture.pdf")
failures = []

def fresh():
    return fitz.open(SRC)

def find_span(doc, needle):
    for b in textedit.layout(doc[0])["blocks"]:
        for ln in b["lines"]:
            for s in ln["spans"]:
                if needle in s["text"]:
                    return s
    raise AssertionError(f"span {needle!r} not found")

def find_block(doc, needle):
    for b in textedit.layout(doc[0])["blocks"]:
        if needle in b["text"]:
            return b
    raise AssertionError(f"block {needle!r} not found")

def verify(label, doc, present=(), absent=()):
    # Reload first: MuPDF caches ToUnicode CMaps, so in-session extraction of
    # freshly embedded fonts is stale even though the saved bytes are correct.
    text = fitz.open(stream=doc.tobytes(), filetype="pdf")[0].get_text()
    for probe in present:
        if probe not in text:
            failures.append(f"{label}: missing {probe!r}")
    for probe in absent:
        if probe in text:
            failures.append(f"{label}: should have removed {probe!r}")
    weird = sorted({c for c in text if ord(c) > 0x7f})
    if weird:
        failures.append(f"{label}: mojibake {[hex(ord(c)) for c in weird]}")
    bad = [f for f in failures if f.startswith(label)]
    print(f"  {label}: {'FAIL' if bad else 'ok'}")

print("1. span edit, text grows")
d = fresh(); s = find_span(d, "4,250,000")
print("   ", textedit.edit_span(d, s["id"], s["text"].replace("$4,250,000", "$9,875,400 (nine million)")))
verify("span-grow", d, present=["$9,875,400 (nine million)", "payable in cash", "Deposit", "Mar 31"],
       absent=["4,250,000,"])

print("2. bold run inside a mixed-style line")
d = fresh(); s = find_span(d, "proposed terms")
print("   ", textedit.edit_span(d, s["id"], "FINAL NEGOTIATED TERMS"))
verify("bold-run", d, present=["FINAL NEGOTIATED TERMS", "This memorandum summarizes",
                               "of the transaction", "no later than"], absent=["proposed terms"])

print("3. paragraph reflow, similar length")
d = fresh(); b = find_block(d, "purchase price")
print("   ", textedit.edit_block(d, b["id"],
      "The purchase price has been revised upward following diligence and is now payable "
      "in two tranches. Seller shall deliver clear title free of all liens and "
      "encumbrances of record, and Buyer waives the financing contingency."))
verify("para-reflow", d, present=["revised upward", "financing contingency", "Deposit", "Mar 31"],
       absent=["$4,250,000"])
d.save(os.path.join(OUT, "edited.pdf"), garbage=4, deflate=True)
fitz.open(os.path.join(OUT, "edited.pdf"))[0].get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(
    os.path.join(OUT, "edited.png"))

print("4. paragraph shortened")
d = fresh(); b = find_block(d, "purchase price")
print("   ", textedit.edit_block(d, b["id"], "Price: $4.25M, cash at closing."))
verify("para-short", d, present=["Price: $4.25M", "Deposit"], absent=["encumbrances"])

print("5. find and replace across document")
d = fresh()
print("   ", textedit.replace_all(d, "Seller", "Vendor"))
verify("replace", d, present=["Vendor shall deliver"], absent=["Seller shall"])

print("\nRESULT:", "PASS" if not failures else "FAIL")
for f in failures:
    print("  -", f)
