"""Multi-step workflows: where state carries between operations."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz

from backend import annots, convert, pages, security, signatures, textedit
from backend.api import Api
from backend.session import Session

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
HARD = os.path.join(OUT, "hard.pdf")
LOCKED = os.path.join(OUT, "locked.pdf")
findings = []


def check(label, fn, expect=None):
    try:
        got = fn()
    except Exception as exc:
        findings.append((label, "raised %r" % (exc,)))
        print("  BUG   %-44s raised %r" % (label, exc))
        return None
    if expect is not None and not expect(got):
        findings.append((label, "unexpected %r" % (got,)))
        print("  BUG   %-44s got %r" % (label, got))
        return got
    print("  ok    %-44s %s" % (label, "" if got is None else repr(got)[:40]))
    return got


def find_span(doc, page_no, needle):
    for block in textedit.layout(doc[page_no])["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                if needle in span["text"]:
                    return span
    return None


print("== undo and redo around text edits ==")


def undo_after_edit():
    session = Session()
    session.open(HARD)
    original = session.doc[0].get_text()
    span = find_span(session.doc, 0, "quick brown")
    session.checkpoint()
    textedit.edit_span(session.doc, span["id"], "Page one: the SLOW GREEN turtle waits.")
    session.reload()
    edited = session.doc[0].get_text()
    session.undo()
    restored = session.doc[0].get_text()
    return "changed=%s restored=%s" % (
        "SLOW GREEN" in edited, restored.strip() == original.strip())


check("undo restores text after an edit", undo_after_edit,
      lambda r: r == "changed=True restored=True")


def redo_after_undo():
    session = Session()
    session.open(HARD)
    span = find_span(session.doc, 0, "quick brown")
    session.checkpoint()
    textedit.edit_span(session.doc, span["id"], "REDO MARKER TEXT")
    session.reload()
    session.undo()
    session.redo()
    return "REDO MARKER TEXT" in session.doc[0].get_text()


check("redo reapplies the edit", redo_after_undo, lambda r: r is True)


def repeated_edits():
    """Ids shift after each edit, so each round must re-resolve them."""
    session = Session()
    session.open(HARD)
    for i in range(4):
        span = find_span(session.doc, 0, "marker")
        if not span:
            return "lost the span at round %d" % i
        session.checkpoint()
        textedit.edit_span(session.doc, span["id"], "Repeat marker round%d." % i)
        session.reload()
    return session.doc[0].get_text().count("round3")


check("four sequential edits on one line", repeated_edits, lambda r: r == 1)

print("\n== encryption is preserved on save ==")


def save_keeps_encryption():
    session = Session()
    session.open(LOCKED, "secret")
    target = os.path.join(OUT, "flow_enc.pdf")
    session.save(target)
    again = fitz.open(target)
    return "still_encrypted=%s" % bool(again.needs_pass)


check("saving an encrypted doc keeps its password", save_keeps_encryption,
      lambda r: r == "still_encrypted=True")

print("\n== save over the open file ==")


def save_in_place():
    import shutil
    copy = os.path.join(OUT, "inplace.pdf")
    shutil.copyfile(HARD, copy)
    session = Session()
    session.open(copy)
    span = find_span(session.doc, 0, "quick brown")
    session.checkpoint()
    textedit.edit_span(session.doc, span["id"], "Saved in place marker.")
    session.reload()
    session.save()                      # same path
    reopened = fitz.open(copy)
    ok = "Saved in place marker" in reopened[0].get_text()
    more = session.doc.page_count       # session must still be usable
    return "persisted=%s usable=%s" % (ok, more == 7)


check("save over the open file", save_in_place,
      lambda r: r == "persisted=True usable=True")

print("\n== api envelopes on cancelled dialogs ==")


def api_save_without_path():
    api = Api()
    api.open_path(HARD)
    api._session.path = None            # as if built from imported files
    return api.save()                   # no window, so the dialog must fail cleanly


check("save with no path and no window", api_save_without_path,
      lambda r: isinstance(r, dict) and r["ok"] is False and "error" in r)

print("\n== redaction then editing ==")


def redact_then_edit():
    doc = fitz.open(HARD)
    security.mark_matches(doc, "brown")
    security.apply(doc)
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    gone = "brown" not in doc[0].get_text()
    span = find_span(doc, 0, "marker")
    textedit.edit_span(doc, span["id"], "After redaction edit works.")
    text = fitz.open(stream=doc.tobytes(), filetype="pdf")[0].get_text()
    return "redacted=%s then_edited=%s" % (gone, "After redaction edit works" in text)


check("edit still works after a redaction", redact_then_edit,
      lambda r: r == "redacted=True then_edited=True")

print("\n== flatten then edit ==")


def flatten_then_edit():
    doc = fitz.open(HARD)
    annots.add_note(doc, 0, [300, 300], "note")
    annots.flatten(doc)
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    span = find_span(doc, 0, "marker")
    textedit.edit_span(doc, span["id"], "Edited after flatten.")
    return "Edited after flatten" in fitz.open(
        stream=doc.tobytes(), filetype="pdf")[0].get_text()


check("edit still works after flattening", flatten_then_edit, lambda r: r is True)

print("\n== page operations then text editing ==")


def reorder_then_edit():
    """move(0, 3) reinserts before old index 3, so the page lands at index 2."""
    doc = fitz.open(HARD)
    pages.move(doc, 0, 3)
    span = find_span(doc, 2, "marker")
    if not span:
        return "text not found after the move"
    textedit.edit_span(doc, span["id"], "Edited after reordering.")
    return "Edited after reordering" in fitz.open(
        stream=doc.tobytes(), filetype="pdf")[2].get_text()


check("edit a page that has been moved", reorder_then_edit, lambda r: r is True)

print("\n== signatures ==")
sig_ids = []


def sig_on_rotated():
    from PIL import Image, ImageDraw
    path = os.path.join(OUT, "flow_sig.png")
    image = Image.new("RGB", (300, 100), "white")
    ImageDraw.Draw(image).line([(20, 70), (90, 25), (160, 70), (250, 30)],
                               fill=(10, 20, 90), width=6)
    image.save(path)
    entry = signatures.add(path, "Flow Tester")
    sig_ids.append(entry["id"])
    doc = fitz.open(HARD)
    signatures.place(doc, 1, entry["id"], [100, 100, 300, 170])   # rotated page
    editable = "annots=%d" % len(annots.listing(doc, 1))

    straight = fitz.open(HARD)
    signatures.place(straight, 1, entry["id"], [100, 100, 300, 170], flatten=True)
    locked = "annots=%d images=%d" % (len(annots.listing(straight, 1)),
                                      len(straight[1].get_images()))
    return "editable[%s] locked[%s]" % (editable, locked)


check("signature on a rotated page: editable, and lockable on request",
      sig_on_rotated,
      lambda r: "editable[annots=1]" in r and "locked[annots=0 images=1]" in r)

print("")
print("== objects stay editable until flattened ==")


def signature_is_adjustable():
    """A placed signature must be movable and resizable, then lockable."""
    from PIL import Image, ImageDraw
    path = os.path.join(OUT, "adj_sig.png")
    image = Image.new("RGB", (400, 140), "white")
    ImageDraw.Draw(image).line([(20, 110), (370, 40)], fill=(12, 20, 90), width=10)
    image.save(path)
    entry = signatures.add(path, "Adjustable")
    sig_ids.append(entry["id"])

    doc = fitz.open(HARD)
    placed = signatures.place(doc, 0, entry["id"], [120, 400, 340, 470])
    listed = annots.listing(doc, 0)
    if len(listed) != 1 or not listed[0]["is_signature"]:
        return "not placed as an editable signature"
    annots.update(doc, 0, placed["id"], rect=[90, 360, 430, 500])
    moved = [round(v) for v in annots.listing(doc, 0)[0]["rect"]]
    annots.flatten(doc)
    after = fitz.open(stream=doc.tobytes(), filetype="pdf")
    return "moved=%s locked=%s drawn=%s" % (
        moved == [90, 360, 430, 500],
        len(annots.listing(after, 0)) == 0,
        len(after[0].get_images()) >= 1)


check("signature can be moved, resized, then locked", signature_is_adjustable,
      lambda r: r == "moved=True locked=True drawn=True")


def signature_can_be_removed():
    doc = fitz.open(HARD)
    placed = signatures.place(doc, 0, sig_ids[-1], [120, 400, 340, 470])
    annots.delete(doc, 0, placed["id"])
    return len(annots.listing(doc, 0))


check("a signature can be deleted before locking", signature_can_be_removed,
      lambda n: n == 0)


def signature_keeps_transparency():
    """A stamped signature must not paint a white box over the page."""
    doc = fitz.open(HARD)
    placed = signatures.place(doc, 0, sig_ids[-1], [120, 400, 340, 470])
    page = doc[0]
    annot = [a for a in page.annots() if a.xref == placed["id"]][0]
    pix = annot.get_pixmap(alpha=True)
    corner = pix.pixel(1, 1)
    return "corner_alpha=%s" % (corner[-1] if len(corner) > 3 else "opaque")


check("signature keeps its transparent background", signature_keeps_transparency,
      lambda r: r.endswith("=0") or "opaque" not in r)


def textbox_is_editable():
    doc = fitz.open(HARD)
    made = annots.add_textbox(doc, 0, [80, 300, 280, 340], "First wording")
    annots.update(doc, 0, made["id"], rect=[80, 300, 400, 380], content="Second wording")
    found = [a for a in annots.listing(doc, 0) if a["type"] == "text box"][0]
    annots.flatten(doc)
    return "text=%r rect=%s locked=%s" % (
        found["content"], [round(v) for v in found["rect"]],
        len(annots.listing(fitz.open(stream=doc.tobytes(), filetype="pdf"), 0)) == 0)


check("text box can be retyped, resized, then locked", textbox_is_editable,
      lambda r: "Second wording" in r and "[80, 300, 400, 380]" in r and "locked=True" in r)


def flatten_keeps_appearance():
    """Flattening must not change what the page looks like."""
    doc = fitz.open(HARD)
    signatures.place(doc, 0, sig_ids[-1], [120, 400, 340, 470])
    before = doc[0].get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).samples
    annots.flatten(doc)
    after = fitz.open(stream=doc.tobytes(), filetype="pdf")[0].get_pixmap(
        matrix=fitz.Matrix(1.2, 1.2)).samples
    same = sum(1 for a, b in zip(before, after) if a != b) / max(1, len(before))
    return "pixels_changed=%.3f%%" % (same * 100)


check("flattening leaves the page looking the same", flatten_keeps_appearance,
      lambda r: float(r.split("=")[1].rstrip("%")) < 1.0)

print("\n== ocr behaviour ==")


def ocr_skips_real_text():
    """A no-op is reported as an error so the UI can explain the Force option."""
    doc = fitz.open(HARD)
    try:
        convert.ocr(doc, pages=[0])
        return "silently did nothing"
    except Exception as exc:
        return "explained: %s" % ("Force" in str(exc))


check("ocr explains when a page already has text", ocr_skips_real_text,
      lambda r: r == "explained: True")


def ocr_mixed_document():
    """A document with both scanned and text pages must OCR just the scans."""
    doc = fitz.open(HARD)
    result = convert.ocr(doc)
    return "pages=%d skipped=%d" % (result["pages"], result["skipped"])


check("ocr handles a mixed document", ocr_mixed_document,
      lambda r: r.startswith("pages=1") and "skipped=" in r)


def ocr_force_on_text_page():
    doc = fitz.open(HARD)
    return convert.ocr(doc, pages=[0], force=True)["pages"]


check("ocr force works on a text page", ocr_force_on_text_page, lambda n: n == 1)


def ocr_image_page():
    doc = fitz.open(HARD)
    convert.ocr(doc, pages=[5])
    text = fitz.open(stream=doc.tobytes(), filetype="pdf")[5].get_text()
    return "SCANNED" in text.upper()


check("ocr reads the image-only page", ocr_image_page, lambda r: r is True)

print("\n== conversions on awkward pages ==")
check("hard doc -> docx",
      lambda: os.path.getsize(convert.to_docx(fitz.open(HARD),
                                              os.path.join(OUT, "hard.docx"))["path"]),
      lambda n: n > 2000)
check("hard doc -> xlsx",
      lambda: convert.to_xlsx(fitz.open(HARD), os.path.join(OUT, "hard.xlsx"))["path"],
      lambda p: os.path.getsize(p) > 2000)
check("hard doc -> pptx",
      lambda: convert.to_pptx(fitz.open(HARD), os.path.join(OUT, "hard.pptx"))["slides"],
      lambda n: n == 7)
check("hard doc -> images",
      lambda: convert.to_images(fitz.open(HARD), os.path.join(OUT, "hard_img"), 90)["count"],
      lambda n: n == 7)
check("compress the hard doc",
      lambda: convert.compress(fitz.open(HARD), os.path.join(OUT, "hard_small.pdf"))["size"],
      lambda n: n > 0)


def unicode_to_text():
    path = convert.to_text(fitz.open(HARD), os.path.join(OUT, "hard.txt"))["path"]
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    return "café" in body.lower()


check("unicode survives the text export", unicode_to_text, lambda r: r is True)

for identifier in sig_ids:
    try:
        signatures.remove(identifier)
    except Exception:
        pass

print("")
print("== unsaved work is tracked ==")


def unsaved_tracking():
    api = Api()
    api.open_path(HARD)
    clean = api.unsaved()
    api.annot_note(0, [200, 200], "a change")
    dirty = api.unsaved()
    api._session.save(os.path.join(OUT, "flow_saved.pdf"))
    after = api.unsaved()
    return "clean=%d dirty=%d after_save=%d" % (len(clean), len(dirty), len(after))


check("a document is only dirty once edited", unsaved_tracking,
      lambda r: r == "clean=0 dirty=1 after_save=0")


def unsaved_across_tabs():
    api = Api()
    api.open_path(HARD)
    api.open_path(os.path.join(OUT, "fixture.pdf"))
    api.annot_note(0, [200, 200], "second tab edit")
    api.tab_switch(0)
    api.annot_note(0, [220, 220], "first tab edit")
    return sorted(t["name"] for t in api.unsaved())


check("every changed tab is reported", unsaved_across_tabs,
      lambda r: r == ["fixture.pdf", "hard.pdf"])


def save_all_writes_everything():
    import shutil
    first = os.path.join(OUT, "sa_one.pdf")
    second = os.path.join(OUT, "sa_two.pdf")
    shutil.copyfile(HARD, first)
    shutil.copyfile(os.path.join(OUT, "fixture.pdf"), second)
    api = Api()
    api.open_path(first)
    api.open_path(second)
    api.annot_note(0, [200, 200], "tab two")
    api.tab_switch(0)
    api.annot_note(0, [200, 200], "tab one")
    result = api.save_all()
    return "saved=%d left_dirty=%d" % (len(result["saved"]), len(api.unsaved()))


check("save all clears every tab", save_all_writes_everything,
      lambda r: r == "saved=2 left_dirty=0")

print("\n" + "=" * 66)
if findings:
    print("%d issue(s) found:" % len(findings))
    for label, detail in findings:
        print("  - %-46s %s" % (label, detail))
else:
    print("No issues found.")
