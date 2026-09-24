"""OCR: a scanned page must become searchable without changing how it looks."""
import io, os, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf as fitz
from backend import convert

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
src = fitz.open(os.path.join(OUT, "fixture.pdf"))

# Flatten to a pure image, the way a scanner would.
pix = src[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
scan = fitz.open()
page = scan.new_page(width=src[0].rect.width, height=src[0].rect.height)
page.insert_image(page.rect, pixmap=pix)
scan.save(os.path.join(OUT, "scanned.pdf"))
before = fitz.open(os.path.join(OUT, "scanned.pdf"))[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5))

fails = []
print("status:", convert.ocr_status())
assert convert.ocr_status()["available"], "OCR engine should need no install"
print("text before OCR:", repr(scan[0].get_text().strip()))

t = time.time()
result = convert.ocr(scan)
print("ocr: %s in %.1fs" % (result, time.time() - t))

scan.save(os.path.join(OUT, "scanned_ocr.pdf"))
done = fitz.open(os.path.join(OUT, "scanned_ocr.pdf"))
text = done[0].get_text()
print("\nrecovered text:\n" + text[:420])

for probe in ["Willmore Capital Partners", "4,250,000", "Deposit", "100,000", "Mar 31"]:
    hit = probe in text
    if not hit:
        fails.append("missing " + repr(probe))
    print("  found %-28r %s" % (probe, hit))

hits = done[0].search_for("purchase price")
print("  search_for('purchase price') ->", len(hits), "hit(s)")
if not hits:
    fails.append("search found nothing")

# The visible page must be untouched: only invisible text was added.
after = done[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
identical = before.samples == after.samples
print("  page pixels unchanged:", identical)
if not identical:
    fails.append("OCR altered the visible page")

done[0].get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).save(os.path.join(OUT, "scanned_ocr.png"))
print("\nRESULT:", "PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
