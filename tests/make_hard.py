"""A deliberately awkward multi-page fixture for edge-case testing."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymupdf as fitz
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUT, exist_ok=True)

doc = fitz.open()

# 1 normal text
p = doc.new_page(width=612, height=792)
p.insert_text((72, 100), "Page one: the quick brown fox jumps.", fontsize=12)
p.insert_text((72, 130), "Repeat marker ABC and ABC again.", fontsize=12)

# 2 rotated 90
p = doc.new_page(width=612, height=792)
p.insert_text((72, 100), "Page two rotated content.", fontsize=12)
p.set_rotation(90)

# 3 cropped (origin not at 0,0 -- catches coordinate bugs)
p = doc.new_page(width=612, height=792)
p.insert_text((150, 300), "Cropped page marker XYZ.", fontsize=12)
p.set_cropbox(fitz.Rect(100, 200, 500, 600))

# 4 unicode / accents / symbols
p = doc.new_page(width=612, height=792)
p.insert_font(fontname="F0", fontfile=r"C:\Windows\Fonts\calibri.ttf")
p.insert_text((72, 100), "Café naïve résumé — €50 £20 «quoted» ½", fontsize=13, fontname="F0")

# 5 blank
doc.new_page(width=612, height=792)

# 6 image only (a "scan")
img = Image.new("RGB", (900, 400), "white")
d = ImageDraw.Draw(img)
d.text((40, 160), "SCANNED INVOICE 8812", fill=(10, 10, 10))
path = os.path.join(OUT, "scan_block.png")
img.save(path)
p = doc.new_page(width=612, height=792)
p.insert_image(fitz.Rect(72, 80, 540, 290), filename=path)

# 7 landscape
doc.new_page(width=792, height=612).insert_text((72, 100), "Landscape page.", fontsize=12)

doc.set_metadata({"title": "Hard fixture", "author": "tests"})
doc.save(os.path.join(OUT, "hard.pdf"))
print("hard.pdf pages:", doc.page_count)

enc = fitz.open(os.path.join(OUT, "hard.pdf"))
enc.save(os.path.join(OUT, "locked.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256,
         user_pw="secret", owner_pw="owner")
print("locked.pdf written")
