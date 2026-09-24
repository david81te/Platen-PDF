"""Exercise the API surface the UI actually calls, without the GUI."""
import io, os, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import Api

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
SRC = os.path.join(OUT, "fixture.pdf")
api = Api()
fails = []

def ok(label, res, want_key=None):
    good = isinstance(res, dict) and res.get("ok") is True
    if good and want_key:
        good = want_key in (res.get("data") or {})
    (print("  PASS  %-30s" % label) if good else
     (fails.append((label, res)), print("  FAIL  %-30s %s" % (label, res))))

ok("ping", api.ping())
ok("open_path", api.open_path(SRC), "page_count")
ok("doc_info", api.doc_info(), "page_count")
ok("render", api.render(0, 1.0), "image")
ok("thumbs", api.thumbs(0, 1, 100))
ok("page_sizes", api.page_sizes())
ok("search", api.search("purchase"))
ok("outline", api.outline())
ok("text_layout", api.text_layout(0), "blocks")

layout = api.text_layout(0)["data"]
span = [s for b in layout["blocks"] for ln in b["lines"] for s in ln["spans"]
        if "4,250,000" in s["text"]][0]
ok("edit_span", api.edit_span(span["id"], span["text"].replace("$4,250,000", "$7,100,000")))

layout = api.text_layout(0)["data"]
block = max(layout["blocks"], key=lambda b: len(b["text"]))
ok("edit_block", api.edit_block(block["id"], "Revised paragraph text for the API check."))
ok("undo", api.undo())
ok("redo", api.redo())
ok("add_text", api.add_text(0, [80, 640, 400, 680], "Added via API", "Calibri", 11))
ok("replace_all", api.replace_all("Seller", "Vendor"))
ok("annot_markup", api.annot_markup(0, "highlight", [[100, 180, 400, 200]]))
ok("annot_note", api.annot_note(0, [300, 300], "API note"))
ok("annot_shape", api.annot_shape(0, "rect", [[100, 500], [300, 560]]))
ok("annot_ink", api.annot_ink(0, [[[100, 400], [150, 430], [200, 400]]]))
ok("annot_stamp", api.annot_stamp(0, [380, 80, 540, 140], "approved"))
ok("annot_list", api.annot_list(0))
ok("link_add", api.link_add(0, [80, 700, 300, 720], "https://example.com"))
ok("link_list", api.link_list(0))
ok("link_delete", api.link_delete(0, 0))
ok("form_add", api.form_add(0, "text", [350, 470, 540, 492], "api_field"))
ok("form_set", api.form_set(0, "api_field", "value"))
ok("form_list", api.form_list())
ok("page_rotate", api.page_rotate([0], 90))
ok("page_insert", api.page_insert(1))
ok("page_duplicate", api.page_duplicate([0]))
ok("page_move", api.page_move(1, 0))
ok("page_delete", api.page_delete([1]))
ok("page_crop", api.page_crop(0, [30, 30, 560, 740]))
ok("page_reset_crop", api.page_reset_crop(0))
ok("watermark", api.watermark("DRAFT"))
ok("stamp_text", api.stamp_text("Page {page} of {pages}"))
ok("background", api.background(None, [1, 1, 0.94]))
ok("redact_mark", api.redact_mark(0, [[100, 180, 200, 200]]))
ok("redact_pending", api.redact_pending(0))
ok("redact_clear", api.redact_clear())
ok("security_info", api.security_info())
ok("ocr_status", api.ocr_status())
ok("sig_list", api.sig_list())
ok("set_metadata", api.set_metadata({"title": "API test"}))
ok("annot_flatten", api.annot_flatten())
ok("close_doc", api.close_doc())

err = api.render(0, 1.0)
print("  PASS  error path                  " if err.get("ok") is False else "  FAIL  error path")
if err.get("ok") is not False:
    fails.append(("error path", err))

print("\nRESULT:", "PASS" if not fails else "FAIL (%d)" % len(fails))
for f in fails:
    print("  ", f)
