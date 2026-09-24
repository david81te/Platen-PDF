"""Edge cases and error paths: the things the happy-path suites never hit."""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz

from backend import annots, convert, forms, pages, security, signatures, textedit
from backend.api import Api
from backend.session import PdfError, Session

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
HARD = os.path.join(OUT, "hard.pdf")
LOCKED = os.path.join(OUT, "locked.pdf")
findings = []


def check(label, fn, expect=None):
    """expect=None means 'must not raise'; otherwise a predicate on the result."""
    try:
        got = fn()
    except Exception as exc:
        findings.append((label, "raised %r" % (exc,)))
        print("  BUG   %-40s raised %r" % (label, exc))
        return None
    if expect is not None and not expect(got):
        findings.append((label, "unexpected %r" % (got,)))
        print("  BUG   %-40s got %r" % (label, got))
        return got
    print("  ok    %-40s %s" % (label, "" if got is None else repr(got)[:46]))
    return got


def must_raise(label, fn):
    try:
        got = fn()
    except Exception:
        print("  ok    %-40s rejected cleanly" % label)
        return
    findings.append((label, "accepted bad input -> %r" % (got,)))
    print("  BUG   %-40s accepted bad input -> %r" % (label, got))


def with_timeout(label, fn, seconds=20):
    """Run fn in a thread so an infinite loop shows up as a finding, not a hang."""
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        findings.append((label, "did not finish in %ds (likely infinite loop)" % seconds))
        print("  BUG   %-40s HUNG (>%ds)" % (label, seconds))
        return None
    if "error" in box:
        findings.append((label, "raised %r" % (box["error"],)))
        print("  BUG   %-40s raised %r" % (label, box["error"]))
        return None
    print("  ok    %-40s %s" % (label, repr(box.get("value"))[:46]))
    return box.get("value")


def fresh():
    return fitz.open(HARD)


print("== encrypted documents ==")
s = Session()
must_raise("open encrypted without password", lambda: s.open(LOCKED))
check("open encrypted with password", lambda: s.open(LOCKED, "secret")["page_count"],
      lambda n: n == 7)
check("encrypted doc renders", lambda: s.render(0, 1.0)["width"], lambda w: w > 0)
check("encrypted doc searchable", lambda: len(s.search("quick")["hits"]), lambda n: n >= 1)


def save_encrypted_roundtrip():
    target = os.path.join(OUT, "enc_resave.pdf")
    s.save(target)
    again = fitz.open(target)
    return "needs_pass=%s" % again.needs_pass


check("save an opened encrypted doc", save_encrypted_roundtrip)
s.close()

print("\n== rotated and cropped pages ==")
d = fresh()
check("layout on rotated page", lambda: len(textedit.layout(d[1])["blocks"]),
      lambda n: n >= 0)
check("markup on rotated page",
      lambda: annots.add_markup(fresh(), 1, "highlight", [[70, 90, 300, 110]])["type"])
check("image on rotated page",
      lambda: annots.add_image(fresh(), 1, [100, 100, 300, 200],
                               os.path.join(OUT, "scan_block.png"))["ok"])

cropped = fresh()[2]
print("    page 3 rect=%s cropbox=%s" % (cropped.rect, cropped.cropbox))


def cropped_coords():
    """A mark placed at the visible top-left must land inside the crop area."""
    doc = fresh()
    page = doc[2]
    rect = page.rect
    target = fitz.Rect(rect.x0 + 10, rect.y0 + 10, rect.x0 + 120, rect.y0 + 40)
    annots.add_shape(doc, 2, "rect", [[target.x0, target.y0], [target.x1, target.y1]])
    placed = annots.listing(doc, 2)[0]["rect"]
    inside = (placed[0] >= rect.x0 - 1 and placed[1] >= rect.y0 - 1
              and placed[2] <= rect.x1 + 1 and placed[3] <= rect.y1 + 1)
    return "placed=%s inside_crop=%s" % ([round(v) for v in placed], inside)


check("shape lands inside cropped page", cropped_coords,
      lambda r: "inside_crop=True" in r)


def cropped_render_geometry():
    session = Session()
    session.open(HARD)
    data = session.render(2, 1.0)
    page = session.doc[2]
    consistent = abs(data["pdf_width"] - page.rect.width) < 0.5
    return "render_w=%s rect_w=%s match=%s" % (
        round(data["pdf_width"]), round(page.rect.width), consistent)


check("cropped page render size matches rect", cropped_render_geometry,
      lambda r: "match=True" in r)

print("\n== unicode ==")
uni = fresh()
spans = [sp for b in textedit.layout(uni[3])["blocks"]
         for ln in b["lines"] for sp in ln["spans"]]
print("    extracted: %r" % (spans[0]["text"] if spans else None))


def unicode_edit():
    doc = fresh()
    found = [sp for b in textedit.layout(doc[3])["blocks"]
             for ln in b["lines"] for sp in ln["spans"] if "Caf" in sp["text"]]
    if not found:
        return "no unicode span found"
    textedit.edit_span(doc, found[0]["id"], "Zürich café — €75 £30 «ok» ¾")
    text = fitz.open(stream=doc.tobytes(), filetype="pdf")[3].get_text()
    return "roundtrip=%s" % ("Zürich café — €75 £30 «ok» ¾" in text)


check("unicode survives an edit", unicode_edit, lambda r: "roundtrip=True" in r)

print("\n== find and replace ==")
with_timeout("replace where result contains the search",
             lambda: textedit.replace_all(fresh(), "ABC", "ABC-ABC"))
with_timeout("replace with a growing substring",
             lambda: textedit.replace_all(fresh(), "a", "aa"))
check("replace that matches nothing",
      lambda: textedit.replace_all(fresh(), "zzzznope", "x")["replaced"],
      lambda n: n == 0)
must_raise("replace with empty search", lambda: textedit.replace_all(fresh(), "", "x"))

print("\n== blank and image-only pages ==")
check("layout of a blank page", lambda: len(textedit.layout(fresh()[4])["blocks"]),
      lambda n: n == 0)
check("render a blank page", lambda: Session().__class__ and fresh()[4]
      .get_pixmap().width, lambda w: w > 0)
must_raise("edit text on a blank page",
           lambda: textedit.edit_block(fresh(), "4:0", "text"))
check("markup on an image-only page (no words)",
      lambda: annots.add_markup(fresh(), 5, "highlight", [[80, 90, 500, 280]])["type"])

print("\n== bad input ==")
doc = fresh()
must_raise("page index out of range", lambda: Session().require())
must_raise("delete every page", lambda: pages.delete(fresh(), list(range(7))))
must_raise("rotate a page that does not exist", lambda: pages.rotate(fresh(), [99], 90))
must_raise("crop to nothing", lambda: pages.crop(fresh(), 0, [10, 10, 12, 12]))
must_raise("split with a bad range", lambda: pages.split(fresh(), os.path.join(OUT, "sp"),
                                                         "ranges", 1, "9-99"))
must_raise("merge a missing file", lambda: pages.merge(fresh(), ["C:/nope/none.pdf"]))
must_raise("merge an encrypted file", lambda: pages.merge(fresh(), [LOCKED]))
must_raise("unknown shape kind", lambda: annots.add_shape(fresh(), 0, "hexagon",
                                                          [[1, 1], [2, 2]]))
must_raise("unknown stamp", lambda: annots.add_stamp(fresh(), 0, [1, 1, 2, 2], "nope"))
must_raise("edit a span id that is gone", lambda: textedit.edit_span(fresh(), "0:99:0:0", "x"))
must_raise("malformed span id", lambda: textedit.edit_span(fresh(), "not-an-id", "x"))
must_raise("form field that does not exist",
           lambda: forms.set_value(fresh(), 0, "nope", "x"))
must_raise("signature id that does not exist",
           lambda: signatures.place(fresh(), 0, "deadbeef", [1, 1, 50, 50]))
must_raise("redact with nothing marked", lambda: security.apply(fresh()))
must_raise("protect with no passwords",
           lambda: security.save_protected(fresh(), os.path.join(OUT, "x.pdf")))

print("\n== forms round trip ==")


def checkbox_csv():
    doc = fresh()
    forms.add_field(doc, 0, "checkbox", [400, 100, 416, 116], "agreed")
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    forms.set_value(doc, 0, "agreed", True)
    csv_path = os.path.join(OUT, "cb.csv")
    forms.export_csv(doc, csv_path)
    with open(csv_path, encoding="utf-8-sig") as fh:
        exported = fh.read()
    forms.set_value(doc, 0, "agreed", False)
    forms.import_csv(doc, csv_path)
    back = [f for f in forms.listing(doc, 0) if f["name"] == "agreed"][0]["value"]
    return "exported=%r reimported=%r" % (exported.splitlines()[1].split(",")[-1], back)


check("checkbox survives CSV round trip", checkbox_csv,
      lambda r: "reimported='Yes'" in r or "reimported=True" in r)


def checkbox_unticked_stays_unticked():
    """bool("Off") is True, so an unticked box used to come back ticked."""
    doc = fresh()
    forms.add_field(doc, 0, "checkbox", [430, 100, 446, 116], "optin")
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    forms.set_value(doc, 0, "optin", "Off")
    return forms.listing(doc, 0)[0]["value"]


check("unticked checkbox from CSV text", checkbox_unticked_stays_unticked,
      lambda v: v in (False, "Off", ""))

print("")
print("== encrypted open flow through the api ==")
enc_api = Api()
check("no password -> asks instead of erroring",
      lambda: enc_api.open_path(LOCKED),
      lambda r: r["ok"] and r["data"].get("needs_password") is True)
check("wrong password is reported as such",
      lambda: enc_api.open_path(LOCKED, "wrong")["data"]["wrong_password"],
      lambda v: v is True)
check("correct password opens the document",
      lambda: enc_api.open_path(LOCKED, "secret")["data"]["page_count"],
      lambda n: n == 7)
check("encrypted doc searchable through the api",
      lambda: len(enc_api.search("quick")["data"]["hits"]), lambda n: n >= 1)
check("encrypted doc still reports encrypted",
      lambda: enc_api.security_info()["data"]["encrypted"], lambda v: v is True)
check("text layout works on an encrypted doc",
      lambda: len(enc_api.text_layout(0)["data"]["blocks"]), lambda n: n >= 1)

print("\n== api error envelopes ==")
api = Api()
check("call before a document is open",
      lambda: api.render(0, 1.0), lambda r: r["ok"] is False)
check("undo with no history", lambda: api.undo(), lambda r: r["ok"] is False)
api.open_path(HARD)
check("api save without a path is not a crash",
      lambda: isinstance(api.doc_info(), dict), lambda r: r is True)
check("out of range page via api",
      lambda: api.render(99, 1.0), lambda r: r["ok"] is False)
check("edit_span bad id via api",
      lambda: api.edit_span("9:9:9:9", "x"), lambda r: r["ok"] is False)

print("\n== performance ==")
big = fitz.open()
for i in range(120):
    page = big.new_page(width=612, height=792)
    page.insert_text((72, 100), "Page %d of the large document." % (i + 1), fontsize=12)
big_path = os.path.join(OUT, "big.pdf")
big.save(big_path)

session = Session()
session.open(big_path)
start = time.time()
thumbs = session.thumbnails(0, 120, 170)
elapsed = time.time() - start
payload = sum(len(t["image"]) for t in thumbs)
print("    120 thumbnails: %.1fs, %.1f MB of base64" % (elapsed, payload / 1e6))
if elapsed > 10:
    findings.append(("thumbnails", "%.1fs for 120 pages -- UI will stall" % elapsed))
    print("  BUG   thumbnail batch is slow")

start = time.time()
session.checkpoint()
print("    one undo checkpoint on 120 pages: %.2fs" % (time.time() - start))
start = time.time()
res = session.search("document")
print("    search 120 pages: %.2fs, %d hits" % (time.time() - start, len(res["hits"])))

print("\n== undo depth memory ==")


def undo_growth():
    sess = Session()
    sess.open(big_path)
    for _ in range(6):
        sess.checkpoint()
    return "snapshots=%d, ~%.1f MB held" % (
        len(sess._undo), sum(len(b) for b in sess._undo) / 1e6)


check("undo snapshots", undo_growth)

print("\n" + "=" * 62)
if findings:
    print("%d issue(s) found:" % len(findings))
    for label, detail in findings:
        print("  - %-42s %s" % (label, detail))
else:
    print("No issues found.")
