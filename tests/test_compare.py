"""Comparing two PDFs: alignment, word diff, visual diff and mark-up."""
import io, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz
from backend import annots, compare, pages, textedit
from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
SRC = os.path.join(OUT, "fixture.pdf")
HARD = os.path.join(OUT, "hard.pdf")
findings = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        findings.append((label, got))
    print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got))


def reword(path, needle, replacement):
    doc = fitz.open(path)
    span = next(s for b in textedit.layout(doc[0])["blocks"]
                for ln in b["lines"] for s in ln["spans"] if needle in s["text"])
    textedit.edit_span(doc, span["id"], span["text"].replace(needle, replacement))
    return fitz.open(stream=doc.tobytes(), filetype="pdf")


print("== nothing changed ==")
same = compare.compare(fitz.open(SRC), fitz.open(SRC))
check("identical files report identical", same["summary"]["identical"], True)
check("and no words move", (same["summary"]["added_words"],
                            same["summary"]["removed_words"]), (0, 0))

print("\n== a single value changed ==")
edited = reword(SRC, "$4,250,000", "$9,875,400")
result = compare.compare(fitz.open(SRC), edited)
check("reported as different", result["summary"]["identical"], False)
check("only a couple of words move", result["summary"]["added_words"],
      lambda n: 0 < n <= 4)
texts = [c for p in result["pages"] for c in p["changes"]]
check("the old value is named", any("4,250,000" in c["before"] for c in texts), True)
check("the new value is named", any("9,875,400" in c["after"] for c in texts), True)
check("and it can be pointed at on the page",
      any(p["added_rects"] for p in result["pages"]), True)

print("\n== a page inserted in the middle ==")
grown = fitz.open(HARD)
pages.insert_blank(grown, 3)
grown[3].insert_text((90, 120), "Brand new page of terms.", fontsize=12)
grown = fitz.open(stream=grown.tobytes(), filetype="pdf")
shifted = compare.compare(fitz.open(HARD), grown)
check("one page added", shifted["summary"]["pages_added"], 1)
check("and the later pages are NOT reported as rewritten",
      shifted["summary"]["pages_changed"], 0)
check("alignment keeps every original page paired",
      sum(1 for p in shifted["pages"] if p["state"] == "same"), 7)

print("\n== a page removed ==")
shrunk = fitz.open(HARD)
pages.delete(shrunk, [2])
shrunk = fitz.open(stream=shrunk.tobytes(), filetype="pdf")
lost = compare.compare(fitz.open(HARD), shrunk)
check("one page removed", lost["summary"]["pages_removed"], 1)
check("the rest still line up", lost["summary"]["pages_changed"], 0)

print("\n== a change the words cannot see ==")
graphic = fitz.open(SRC)
graphic[0].draw_rect(fitz.Rect(300, 300, 480, 380), color=(0.2, 0.3, 0.8),
                     fill=(0.85, 0.9, 1), width=2)
graphic = fitz.open(stream=graphic.tobytes(), filetype="pdf")
visual = compare.compare(fitz.open(SRC), graphic)
check("a drawn box is caught by the visual pass",
      sum(len(p["visual_rects"]) for p in visual["pages"]), lambda n: n >= 1)
check("even though no words changed",
      visual["summary"]["added_words"] + visual["summary"]["removed_words"], 0)
check("so the page is still reported as changed",
      visual["summary"]["pages_changed"], 1)
off = compare.compare(fitz.open(SRC), graphic, visual=False)
check("the visual pass can be switched off",
      sum(len(p["visual_rects"]) for p in off["pages"]), 0)

print("\n== marking the differences on the page ==")
target = reword(SRC, "$4,250,000", "$9,875,400")
res = compare.compare(fitz.open(SRC), target)
marks = compare.mark_up(target, res)
check("marks were added", marks["marked"], lambda n: n >= 1)
listed = annots.listing(target, 0)
check("additions are highlighted",
      any(a["type"] == "highlight" for a in listed), True)
check("removals leave a note explaining what went",
      any(a["type"] == "note" and "removed" in (a["content"] or "") for a in listed), True)

print("\n== through the api ==")
api = Api()
api.open_path(SRC)
api.open_path(os.path.join(OUT, "revised.pdf"))
env = api.compare_tab(0)
check("compare_tab returns a result", env["ok"], True)
check("and names what it compared against", env["data"]["against"], "fixture.pdf")
check("the result is remembered", api.compare_result()["data"]["summary"] is not None, True)
check("marking up works from the api", api.compare_markup()["data"]["marked"],
      lambda n: n >= 1)
check("and it can be cleared", api.compare_clear()["ok"], True)
check("clearing really clears", api.compare_result()["data"]["summary"], None)
check("comparing a tab with itself is refused",
      api.compare_tab(api._active)["ok"], False)
check("marking up with no comparison is refused", api.compare_markup()["ok"], False)

print("\n" + "=" * 68)
print("No issues found." if not findings else "%d issue(s): %s" % (len(findings), findings))
sys.exit(1 if findings else 0)
