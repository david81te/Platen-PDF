"""Editing text changes the words asked for, and nothing else on the page.

Each check here is something a review found an edit doing to text, comments,
links or redaction marks that nobody asked it to touch - or to text it could
not read - and that the other suites did not see.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401  - keeps the signature library out of the real folder

import pymupdf as fitz

from backend import decorate, fonts, security, textedit
from backend.api import Api

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-64s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def reopened(doc):
    return fitz.open("pdf", doc.tobytes())


def spans(page):
    return [(s["id"], s["text"], s["font"]) for b in textedit.layout(page)["blocks"]
            for l in b["lines"] for s in l["spans"]]


def span_id(doc, starting):
    return next(i for i, text, _ in spans(doc[0]) if text.replace("\xa0", " ").startswith(starting))


def words(doc):
    return " ".join(reopened(doc)[0].get_text().replace("\xa0", " ").split())


print("== find and replace ==")
# One line in three runs, the middle one bold: the second match used to be
# drawn with a font the first edit's redaction had taken off the page -
# after its words were removed. The whole line vanished.
doc = fitz.open()
page = doc.new_page()
page.insert_font(fontname="AR", fontfile=os.path.join(FONTS, "arial.ttf"))
page.insert_font(fontname="AB", fontfile=os.path.join(FONTS, "arialbd.ttf"))
x = 72
for text, alias, face in (("foo is ", "AR", "arial.ttf"), ("bold", "AB", "arialbd.ttf"),
                          (" and foo", "AR", "arial.ttf")):
    page.insert_text((x, 100), text, fontname=alias, fontsize=12)
    x += fitz.Font(fontfile=os.path.join(FONTS, face)).text_length(text, 12)
doc = reopened(doc)
result = textedit.replace_all(doc, "foo", "bazzz")
check("both matches replaced, nothing skipped", (result["replaced"], result["skipped"]), (2, 0))
check("and the line is all still there", words(doc), "bazzz is bold and bazzz")

print("== what else is on the page ==")
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 100), "Name: John and our website here", fontsize=12)
page.insert_text((72, 300), "SECRET account 12345", fontsize=12)
page.add_freetext_annot(fitz.Rect(110, 88, 200, 104), "John Smith")
page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(160, 88, 230, 104),
                  "uri": "https://example.com"})
security.mark(doc, 0, [[70, 285, 250, 305]])
del page
doc = reopened(doc)
textedit.edit_span(doc, span_id(doc, "Name"), "Name: Jane and our website here")
check("a redaction marked but not applied stays unapplied",
      (security.pending(doc, 0), "SECRET" in doc[0].get_text()), (1, True))
check("a comment over the line is kept",
      [a.type[1] for a in doc[0].annots() if a.type[1] == "FreeText"], ["FreeText"])
check("a link on the line is kept", len(doc[0].get_links()), 1)
check("and the edit was made", "Jane" in words(doc), True)

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 80), "Body text line", fontsize=11)
field = fitz.Widget()
field.field_type, field.field_name = fitz.PDF_WIDGET_TYPE_TEXT, "f1"
field.rect, field.field_value = fitz.Rect(70, 150, 250, 170), "typed"
page.add_widget(field)
page.add_freetext_annot(fitz.Rect(70, 300, 300, 330), "Freetext comment", fontsize=11)
del page
doc = reopened(doc)
check("comments and form fields are not offered as page text",
      [text for _, text, _ in spans(doc[0])], ["Body text line"])

print("== characters past Latin ==")
doc = fitz.open()
page = doc.new_page()
page.insert_font(fontname="AR", fontfile=os.path.join(FONTS, "arial.ttf"))
before = fonts.page_font_xrefs(doc, 0)
page.insert_text((72, 100), "Привет Ωmega € Łódź", fontname="AR", fontsize=12)
fonts.repair_inserted_fonts(doc, 0, before)
check("Cyrillic and Greek read back after the font is repaired",
      words(doc), "Привет Ωmega € Łódź")

doc = fitz.open()
doc.new_page().insert_text((72, 100), "Total due", fontname="tiro", fontsize=12)
doc = reopened(doc)
textedit.edit_span(doc, span_id(doc, "Total"), "Total – €5 “ok”")
check("a dash, euro and curly quotes are drawn, not dots", words(doc), "Total – €5 “ok”")

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 100), "Plain", fontname="helv", fontsize=12)
page.insert_text((140, 100), "BOLD", fontname="hebo", fontsize=12)
del page
doc = reopened(doc)
textedit.edit_span(doc, span_id(doc, "BOLD"), "BOLDER")
check("bold Helvetica stays bold", [f for _, t, f in spans(reopened(doc)[0]) if "BOLDER" in t],
      ["Helvetica-Bold"])

doc = fitz.open()
doc.new_page()
decorate.stamp(doc, "headfoot", {"top-center": "Café – “quoted” €5 • Страница {page}"})
check("a header in any script reads back as written",
      words(doc), "Café – “quoted” €5 • Страница 1")

print("== text it cannot read, or must not show ==")
doc = fitz.open()
page = doc.new_page()
page.insert_font(fontname="F9", fontfile=os.path.join(FONTS, "georgia.ttf"))
page.insert_text((72, 100), "Hello", fontname="F9", fontsize=12)
page.insert_text((110, 100), "world and more", fontname="helv", fontsize=12)
del page
doc = reopened(doc)
for xref in range(1, doc.xref_length()):
    if "/ToUnicode" in doc.xref_object(xref):
        doc.xref_set_key(xref, "ToUnicode", "null")
doc = reopened(doc)
# Its text reads "+HOOR"; redrawn from that, "Hello" became "+HOOR" on the page.
hello = fitz.Rect(60, 85, 100, 105)
shown_before = doc[0].get_pixmap(clip=hello).samples
textedit.edit_span(doc, span_id(doc, "world"), "earth and more")
check("a neighbour in a font without a character map is left as drawn",
      doc[0].get_pixmap(clip=hello).samples == shown_before, True)

scan = fitz.open()
scan.new_page().insert_text((72, 100), "Invoice number 12345 due", fontsize=14)
picture = scan[0].get_pixmap(dpi=150)
doc = fitz.open()
page = doc.new_page()
page.insert_image(page.rect, pixmap=picture)
page.insert_text((72, 100), "Invoice number 12345 due", fontsize=14, render_mode=3)
del page
doc = reopened(doc)
ink = doc[0].get_pixmap(clip=fitz.Rect(60, 80, 300, 110)).samples
textedit.edit_span(doc, span_id(doc, "Invoice"), "Invoice number 99999 due")
check("recognised text over a scan stays invisible when edited",
      (doc[0].get_pixmap(clip=fitz.Rect(60, 80, 300, 110)).samples == ink,
       "99999" in doc[0].get_text()), (True, True))

doc = fitz.open()
doc.new_page().insert_htmlbox(fitz.Rect(72, 80, 400, 120), "<p dir='rtl'>שלום עולם</p>")
doc = reopened(doc)
try:
    textedit.edit_span(doc, spans(doc[0])[0][0], "שלום")
    refused = False
except textedit.EditRefused:
    refused = True
check("right-to-left text is refused, not drawn backwards", refused, True)

print("== right-aligned figures ==")
doc = fitz.open()
page = doc.new_page()
helv = fitz.Font("helv")
for x in (72, 300, 400):
    page.draw_line((x, 80), (x, 200))
for row, (item, amount) in enumerate((("Widgets", "1,234.00"), ("Gadgets", "56.00"),
                                      ("Total", "1,290.00"))):
    page.insert_text((76, 100 + 20 * row), item, fontsize=11)
    page.insert_text((396 - helv.text_length(amount, 11), 100 + 20 * row), amount, fontsize=11)
del page
doc = reopened(doc)
result = textedit.edit_span(doc, span_id(doc, "56.00"), "5,600.00")
grown = next(b["bbox"] for b in textedit.layout(doc[0])["blocks"] if "5,600.00" in b["text"])
check("a figure in a right-aligned column grows to the left, full size",
      (result["shrunk"], round(grown[2])), (False, 396))

print("== through the window's api ==")
path = os.path.join(sandbox.DIR, "refusal.pdf")
doc = fitz.open()
doc.new_page().insert_htmlbox(fitz.Rect(72, 80, 400, 120), "<p dir='rtl'>שלום עולם</p>")
doc.save(path)
api = Api()
api.open_path(path)
reply = api.edit_span(textedit.layout(api._session.doc[0])["blocks"][0]["lines"][0]["spans"][0]["id"],
                      "שלום")
check("a refused edit says why", (reply["ok"], "Right-to-left" in reply.get("error", "")),
      (False, True))
check("and leaves no undo step behind", api._session.info()["can_undo"], False)


def made(name, count, broken=False):
    path = os.path.join(sandbox.DIR, name)
    doc = fitz.open()
    for number in range(count):
        doc.new_page().insert_text((72, 72), "%s p%d" % (name, number + 1))
    if broken:
        doc.xref_set_key(doc[1].xref, "Resources", "<< /Font 7 >>")
    doc.save(path)
    return path


api = Api()
for name, count, broken in (("A.pdf", 2, False), ("B.pdf", 3, True), ("C.pdf", 2, False)):
    api.open_path(made(name, count, broken))
api.tab_switch(0)
reply = api.bates(prefix="X", start=1, all_tabs=True)
state = [(s.dirty, len(s._undo), "X0000" in "".join(p.get_text() for p in s.doc))
         for s in api._docs]
check("Bates numbering that fails on one document says which", (reply["ok"],
      "B.pdf" in reply.get("error", "")), (False, True))
check("and leaves every document as it was", state, [(False, 0, False)] * 3)

print("\nRESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
