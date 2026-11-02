"""Editing one line must leave every other row of the page exactly as it was.

The reported bug: save an edit and text above, below or beside it vanished.
Two causes. A line that grew wrapped its tail onto the row beneath and drew
over it; and the redaction that removes the old glyphs was the line's box
padded by a point, which on closely set lines covers enough of the rows above
and below that MuPDF strips their glyphs too. The fixtures from Word are set
loosely enough to hide both, so this page is built to expose them: tight
leading, embedded fonts with tall boxes, a table row with ruled cells, a bold
run mid-sentence, a boxed label and a superscript.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz

from backend import textedit

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
failures = []


def font_file(name):
    path = os.path.join(FONTS, name)
    return path if os.path.exists(path) else None


FACES = {"F1": font_file("arial.ttf"), "F1B": font_file("arialbd.ttf"),
         "F2": font_file("georgia.ttf")}
METRICS = {alias: fitz.Font(fontfile=path) if path else fitz.Font("helv")
           for alias, path in FACES.items()}


def build():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for alias, path in FACES.items():
        if path:
            page.insert_font(fontname=alias, fontfile=path)

    def put(x, y, text, alias="F1", size=11.0):
        name = alias if FACES[alias] else "helv"
        page.insert_text((x, y), text, fontname=name, fontsize=size)
        return x + METRICS[alias].text_length(text, fontsize=size)

    # An address set solid: baselines exactly one em apart.
    for i, text in enumerate(["Willmore Capital Partners", "1200 Market Street, Suite 400",
                              "Philadelphia, PA 19107", "Tel 215 555 0142"]):
        put(72, 100 + 11 * i, text)

    # A ruled table: header and one row, cells at fixed columns.
    for y, cells in ((180, ("Item", "Amount", "Due")), (194, ("Deposit", "$100,000", "Jan 15"))):
        for x, text in zip((72, 250, 400), cells):
            put(x, y, text)
    for x in (240, 390):
        page.draw_line((x, 168), (x, 198), width=0.75)

    # A sentence with a bold run in the middle of it.
    x = put(72, 240, "The parties intend to close ")
    x = put(x, 240, "no later than", "F1B")
    put(x, 240, " the end of the quarter.")

    # Prose set tighter than solid, in a font with a tall box.
    prose = ["Seller shall deliver good and marketable title to the Property, free of",
             "all liens and encumbrances, at the Closing, together with the keys and",
             "every document reasonably required to complete the transfer, and shall",
             "indemnify Buyer against any claim arising before the Closing Date."]
    for i, text in enumerate(prose):
        put(72, 280 + 10.45 * i, text, "F2")

    # A label inside a drawn box.
    page.draw_rect(fitz.Rect(72, 340, 300, 360), width=0.75)
    put(76, 354, "Ref: A-12")

    # A superscript riding on its line.
    x = put(72, 400, "Clause 4")
    x = put(x, 396, "2", size=7)
    put(x, 400, " applies here.")
    return doc


def plain(text):
    # Arial maps the space and the no-break space to one glyph, and so does the
    # hyphen with the soft hyphen; the text layer names whichever comes first.
    return text.replace("\xa0", " ").replace("\xad", "-")


def words(doc):
    page = fitz.open(stream=doc.tobytes(), filetype="pdf")[0]
    return [(plain(w[4]), fitz.Rect(w[:4])) for w in page.get_text("words")]


def find_span(doc, needle):
    for block in textedit.layout(doc[0])["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                if plain(span["text"]).strip() == needle:
                    return span
    raise AssertionError("no span reads %r" % needle)


def same_place(a, b, tol=0.6):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def case(label, needle, replacement, moves=None, right_limit=None, check=None,
         expect=None):
    """Edit the run reading `needle`; nothing outside `moves` may change.

    `moves` is the strip of the row that is allowed to be redrawn, as
    (x0, x1); it defaults to the edited run onward to the right margin.
    `expect` lists the words that must then read on the row, when extraction
    would not split the replacement the way `str.split` does.
    """
    doc = build()
    before = words(doc)
    span = find_span(doc, needle)
    base = span["origin"][1]
    size = span["size"]
    x0, x1 = moves or (span["bbox"][0] - 0.5, 612)
    row = (base - 0.9 * size, base + 0.3 * size)

    def in_strip(rect):
        middle = (rect.y0 + rect.y1) / 2
        return x0 <= rect.x0 < x1 and row[0] <= middle <= row[1]

    try:
        result = textedit.edit_span(doc, span["id"], replacement)
    except Exception as exc:
        failures.append("%s: raised %s" % (label, exc))
        print("  FAIL", label, exc)
        return
    after = words(doc)
    problems = []

    for text, rect in before:
        if in_strip(rect):
            continue
        if not any(t == text and same_place(r, rect) for t, r in after):
            problems.append("lost or moved %r" % text)

    for word in expect or replacement.split():
        hits = [r for t, r in after if t == word and in_strip(r)]
        if not hits:
            problems.append("new word %r missing from its row" % word)
        elif right_limit is not None and max(r.x1 for r in hits) > right_limit + 0.5:
            problems.append("new word %r runs past x=%s" % (word, right_limit))

    if any(r.x1 > 612 or r.x0 < 0 for _, r in after):
        problems.append("text off the page")
    if check:
        problems.extend(check(after))

    failures.extend("%s: %s" % (label, p) for p in problems)
    print("  %s %-34s scale=%-6s %s" % ("FAIL" if problems else "ok  ", label,
                                        result.get("scale"), "; ".join(problems)))


print("== one row changes, every other row stays put ==")
case("address line, much longer", "1200 Market Street, Suite 400",
     "1200 Market Street, Suite 400, Philadelphia County, Commonwealth of Pennsylvania 19107")
case("address line, shorter", "Philadelphia, PA 19107", "Phila PA")
case("table header grows into its cell", "Amount",
     "Amount payable in immediately available funds", moves=(245, 390), right_limit=390)
case("table cell grows into its cell", "$100,000",
     "$100,000,000.00 (one hundred million dollars)", moves=(245, 390), right_limit=390)
case("bold run pushes its sentence along", "no later than",
     "NO LATER THAN THE CLOSE OF BUSINESS ON", moves=(72, 612),
     check=lambda after: [] if any(t == "quarter." for t, _ in after)
     else ["the rest of the sentence was lost"])
case("prose line, longer", "all liens and encumbrances, at the Closing, together with the keys and",
     "all liens, charges and encumbrances of record, at the Closing, together with the keys and")
case("boxed label stays in its box", "Ref: A-12",
     "Ref: A-12 / B-7 (revised third draft, superseding all earlier drafts)", right_limit=300)


def superscript_still_raised(_after):
    # The redrawn superscript must keep its own baseline, above the line's.
    doc = build()
    textedit.edit_span(doc, find_span(doc, "Clause 4")["id"], "Clause 14")
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"] == "2" and span["size"] < 8:
                    return [] if span["origin"][1] < 398 else ["superscript flattened onto the line"]
    return ["superscript lost"]


# Extraction glues the raised "2" onto "14", so only "Clause" reads as a word.
case("superscript keeps its height", "Clause 4", "Clause 14", moves=(72, 612),
     check=superscript_still_raised, expect=["Clause"])

print("\nRESULT:", "PASS" if not failures else "FAIL")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
