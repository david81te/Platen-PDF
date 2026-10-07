"""Printing.

The spooler hand-off is the one part that cannot be exercised without sending
paper through a real printer, so everything up to it is: the page range people
type, the scaling onto the printable area, and the actual draw of a rendered
page onto a Windows device context. The last of those is done against a memory
DC, which takes the same calls a printer DC does.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402

import pymupdf as fitz  # noqa: E402

from backend import printing  # noqa: E402
from backend.api import Api  # noqa: E402
from backend.session import PdfError  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "output", "fixture.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


print("== the page range, as people actually type it ==")
for text, count, expected in [
    ("", 3, [0, 1, 2]),
    ("1", 5, [0]),
    ("2-4", 9, [1, 2, 3]),
    ("1-3,7", 9, [0, 1, 2, 6]),
    ("1-3, 7", 9, [0, 1, 2, 6]),          # a space after the comma
    ("1-3;7", 9, [0, 1, 2, 6]),           # a semicolon instead
    ("3-1", 5, [0, 1, 2]),                # backwards
    ("2,2,2", 5, [1]),                    # repeats collapse
    ("7", 3, []),                         # past the end
    ("1-99", 3, [0, 1, 2]),               # clamped to the document
    ("-3", 5, [0, 1, 2]),                 # open start
    ("4-", 5, [3, 4]),                    # open end
    ("abc", 3, []),                       # nonsense
    ("1,abc,3", 5, [0, 2]),               # nonsense among the good
    ("  ", 4, [0, 1, 2, 3]),              # whitespace is empty
]:
    check("%-10r of %d pages" % (text, count), printing.parse_range(text, count), expected)

print("")
print("== fitting a page onto the paper ==")
box = printing.fit_box(612, 792, 2480, 3508)
check("portrait page is centred horizontally",
      box[0] == (2480 - (box[2] - box[0])) // 2, True)
# 612x792 is relatively wider than 2480x3508, so the width is what binds and
# the spare room shows above and below. The first version of this asserted
# the height filled, which was wrong about which dimension decides.
check("the binding dimension fills the paper", box[2] - box[0], 2480)
check("and the other leaves a margin", box[3] - box[1], lambda h: h < 3508)
check("the ratio is kept",
      round((box[2] - box[0]) / (box[3] - box[1]), 3), lambda r: abs(r - 612 / 792) < 0.01)

wide = printing.fit_box(842, 595, 2480, 3508)
check("a landscape page is centred vertically",
      wide[1] == (3508 - (wide[3] - wide[1])) // 2, True)
check("and is not stretched to the full height", wide[3] - wide[1], lambda h: h < 2000)
check("nothing lands outside the paper",
      box[0] >= 0 and box[1] >= 0 and box[2] <= 2480 and box[3] <= 3508, True)
check("a zero-size page does not divide by zero",
      printing.fit_box(0, 0, 100, 200), (0, 0, 100, 200))
check("a zero-size paper does not either",
      printing.fit_box(612, 792, 0, 0), lambda b: b[2] >= 1 and b[3] >= 1)

print("")
print("== the printers this machine has ==")
found = printing.printers()
check("a list comes back", isinstance(found["printers"], list), True)
check("and a default is named", bool(found["default"]), True)
check("the default is one of them",
      found["default"] in found["printers"] or not found["printers"], True)

print("")
print("== a rendered page really draws onto a device context ==")
# A memory DC takes the same calls a printer DC does, so this exercises the
# render, the scale and the blit without sending paper through anything.
import win32gui  # noqa: E402
import win32ui  # noqa: E402
from PIL import Image, ImageWin  # noqa: E402

doc = fitz.open(FIXTURE)
page = doc[0]
pixmap = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
check("the page rendered", (image.width, image.height), lambda s: s[0] > 400 and s[1] > 500)

AREA = (1240, 1754)
# A memory DC needs a real one to be compatible with, and the desktop will do.
# It takes the same drawing calls a printer DC does.
screen_handle = win32gui.GetDC(0)
desktop = win32ui.CreateDCFromHandle(screen_handle)
memory = desktop.CreateCompatibleDC()
bitmap = win32ui.CreateBitmap()
bitmap.CreateCompatibleBitmap(desktop, AREA[0], AREA[1])
memory.SelectObject(bitmap)
memory.FillSolidRect((0, 0, AREA[0], AREA[1]), 0x00FFFFFF)

target = printing.fit_box(image.width, image.height, AREA[0], AREA[1])
ImageWin.Dib(image).draw(memory.GetHandleOutput(), target)

raw = bitmap.GetBitmapBits(True)
drawn = Image.frombuffer("RGBA", AREA, raw, "raw", "BGRA", 0, 1).convert("RGB")
check("something was drawn", drawn.getextrema(), lambda e: any(lo != hi for lo, hi in e))

# The fixture has dark text on white. Inside the target box there must be ink;
# outside it, nothing but the white we filled.
inside = drawn.crop((target[0] + 4, target[1] + 4, target[2] - 4, target[3] - 4))
check("there is ink inside the page area",
      min(inside.convert("L").getextrema()), lambda v: v < 128)
if target[1] > 6:
    above = drawn.crop((0, 0, AREA[0], target[1] - 4))
    check("the margin above the page is untouched",
          above.convert("L").getextrema(), (255, 255))
if target[0] > 6:
    beside = drawn.crop((0, 0, target[0] - 4, AREA[1]))
    check("and the margin beside it",
          beside.convert("L").getextrema(), (255, 255))

# PyCBitmap has no DeleteObject; it is released when it goes out of scope.
memory.DeleteDC()
desktop.DeleteDC()
win32gui.ReleaseDC(0, screen_handle)
doc.close()

print("")
print("== a printer that writes a file instead of printing ==")
found = printing.printers()
check("those are reported separately", isinstance(found.get("to_file"), list), True)
to_pdf = "Microsoft Print to PDF"
if to_pdf in found["printers"]:
    check("Print to PDF is known to need a destination",
          printing.needs_destination(to_pdf), True)

    print("")
    print("== and the document really prints ==")
    import tempfile  # noqa: E402

    target = os.path.join(tempfile.gettempdir(), "platen-print-test.pdf")
    if os.path.exists(target):
        os.remove(target)
    source = fitz.open(FIXTURE)
    outcome = printing.print_document(source, to_pdf, title="test",
                                      destination=target)
    source.close()
    check("the job was accepted", outcome["sheets"], 1)
    check("a file came out", os.path.isfile(target), True)

    printed = fitz.open(target)
    page = printed[0]
    check("one page", printed.page_count, 1)
    check("at the right size",
          (round(page.rect.width), round(page.rect.height)), (612, 792))
    # Printing rasterises, so there is no text to find. What matters is that
    # the page is not blank - a printer that silently emits white paper is the
    # worst possible outcome and the easiest to miss.
    check("the page carries an image", len(page.get_images()), lambda n: n >= 1)
    pix = page.get_pixmap(dpi=100)
    dark = sum(1 for y in range(0, pix.height, 3) for x in range(0, pix.width, 3)
               if sum(pix.pixel(x, y)) / 3 < 140)
    check("and it is not blank", dark, lambda n: n > 100)
    printed.close()
    os.remove(target)
else:
    print("  SKIP Microsoft Print to PDF is not installed on this machine")

print("")
print("== refusing what it cannot do, in words ==")
api = Api()
answer = api.print_document()
check("no document gives a plain message", answer.get("error"), "No document is open.")

api.open_path(FIXTURE)
answer = api.print_document(printer="A Printer That Is Not There")
check("an unknown printer says so",
      "Could not reach the printer" in (answer.get("error") or ""), True)

answer = api.print_document(pages="900-999")
check("a range with no real pages says so",
      "does not include any pages" in (answer.get("error") or ""), True)

listed = api.printers()
check("the printer list is reachable from the API", listed["ok"], True)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
