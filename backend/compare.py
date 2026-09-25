"""Compare two PDFs and report what changed.

Three passes, because no single one is enough:

  pages   Documents rarely line up one-to-one once a page has been inserted or
          removed, so pages are aligned by how similar their text is before
          anything else is compared. Without this, inserting one page reports
          every later page as rewritten.
  words   A word-level diff of each aligned pair, carrying the position of
          every word so a change can be pointed at on the page.
  pixels  A coarse image comparison, which catches what the word diff cannot:
          a moved logo, a redrawn chart, changed shading.
"""
from __future__ import annotations

import difflib
import re

import pymupdf as fitz

from .session import PdfError

# Ignore differences smaller than this fraction of a block's pixels; PDF
# rendering is not bit-exact and anti-aliasing alone will differ slightly.
PIXEL_TOLERANCE = 0.06
GRID = 24                      # blocks across the page for the visual pass
MIN_PAGE_SIMILARITY = 0.35     # below this, two pages are not the same page


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def _page_words(page: fitz.Page) -> list[tuple[str, fitz.Rect]]:
    """Words in reading order, top to bottom then left to right.

    Extraction returns words in content-stream order, which is not reading
    order once anything has been edited or drawn out of sequence: a rewritten
    line lands at the end of the stream and the diff then reports the whole
    line as moved rather than the one word that changed.
    """
    words = [(w[4], fitz.Rect(w[0], w[1], w[2], w[3])) for w in page.get_text("words")]
    # Round the baseline so words on the same visual line sort together even
    # when their boxes differ by a fraction of a point.
    return sorted(words, key=lambda pair: (round(pair[1].y0 / 4.0), pair[1].x0))


def _merge_rects(rects: list[fitz.Rect], gap: float = 3.0) -> list[list[float]]:
    """Join words that sit on the same line into one highlight."""
    out: list[fitz.Rect] = []
    for rect in sorted(rects, key=lambda r: (round(r.y0, 1), r.x0)):
        if out:
            last = out[-1]
            same_line = abs(last.y0 - rect.y0) < 3 and abs(last.y1 - rect.y1) < 3
            if same_line and rect.x0 - last.x1 <= gap + 6:
                out[-1] = last | rect
                continue
        out.append(fitz.Rect(rect))
    return [[r.x0, r.y0, r.x1, r.y1] for r in out]


def align_pages(left: fitz.Document, right: fitz.Document) -> list[dict]:
    """Pair pages by text similarity, so an inserted page shifts nothing else."""
    left_text = [_normalise(left[i].get_text()) for i in range(left.page_count)]
    right_text = [_normalise(right[i].get_text()) for i in range(right.page_count)]

    matcher = difflib.SequenceMatcher(None, left_text, right_text, autojunk=False)
    pairs: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                pairs.append({"left": i1 + offset, "right": j1 + offset, "state": "same"})
        elif tag == "replace":
            # Match them up in order, and report the overhang as added or removed.
            span = min(i2 - i1, j2 - j1)
            for offset in range(span):
                a, b = i1 + offset, j1 + offset
                ratio = difflib.SequenceMatcher(
                    None, left_text[a], right_text[b], autojunk=False).quick_ratio()
                state = "changed" if ratio >= MIN_PAGE_SIMILARITY else "replaced"
                pairs.append({"left": a, "right": b, "state": state})
            for a in range(i1 + span, i2):
                pairs.append({"left": a, "right": None, "state": "removed"})
            for b in range(j1 + span, j2):
                pairs.append({"left": None, "right": b, "state": "added"})
        elif tag == "delete":
            for a in range(i1, i2):
                pairs.append({"left": a, "right": None, "state": "removed"})
        elif tag == "insert":
            for b in range(j1, j2):
                pairs.append({"left": None, "right": b, "state": "added"})
    return pairs


def _word_diff(left: fitz.Page | None, right: fitz.Page | None) -> dict:
    """Word-level differences between one pair of pages."""
    left_words = _page_words(left) if left is not None else []
    right_words = _page_words(right) if right is not None else []
    a = [w.lower() for w, _ in left_words]
    b = [w.lower() for w, _ in right_words]

    removed: list[fitz.Rect] = []
    added: list[fitz.Rect] = []
    samples: list[dict] = []

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        gone = [left_words[i][0] for i in range(i1, i2)]
        came = [right_words[j][0] for j in range(j1, j2)]
        removed.extend(left_words[i][1] for i in range(i1, i2))
        added.extend(right_words[j][1] for j in range(j1, j2))
        if gone or came:
            samples.append({
                "kind": tag,                       # replace | delete | insert
                "before": " ".join(gone)[:140],
                "after": " ".join(came)[:140],
            })

    return {
        "removed_rects": _merge_rects(removed),
        "added_rects": _merge_rects(added),
        "removed_words": len(removed),
        "added_words": len(added),
        "changes": samples,
    }


def _visual_diff(left: fitz.Page | None, right: fitz.Page | None,
                 dpi: int = 72) -> list[list[float]]:
    """Coarse pixel comparison, for changes the word diff cannot see."""
    if left is None or right is None:
        return []
    try:
        import numpy as np
    except ImportError:
        return []

    zoom = dpi / 72.0
    box = right.rect
    left_pix = left.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    right_pix = right.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    if left_pix.width != right_pix.width or left_pix.height != right_pix.height:
        return []                       # different page size: leave it to the pair state

    shape = (right_pix.height, right_pix.width, right_pix.n)
    a = np.frombuffer(left_pix.samples, dtype=np.uint8).reshape(shape).astype(np.int16)
    b = np.frombuffer(right_pix.samples, dtype=np.uint8).reshape(shape).astype(np.int16)
    delta = np.abs(a - b).max(axis=2) > 28

    height, width = delta.shape
    step_y = max(1, height // GRID)
    step_x = max(1, width // GRID)
    regions: list[list[float]] = []
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            block = delta[y:y + step_y, x:x + step_x]
            if block.size and block.mean() > PIXEL_TOLERANCE:
                regions.append([
                    box.x0 + x / zoom, box.y0 + y / zoom,
                    box.x0 + min(x + step_x, width) / zoom,
                    box.y0 + min(y + step_y, height) / zoom,
                ])
    return regions


def compare(left: fitz.Document, right: fitz.Document,
            visual: bool = True) -> dict:
    """Compare two documents. `left` is the older one, `right` the newer."""
    if left.page_count == 0 or right.page_count == 0:
        raise PdfError("Both documents need at least one page.")

    pairs = align_pages(left, right)
    pages = []
    totals = {"added_words": 0, "removed_words": 0,
              "pages_changed": 0, "pages_added": 0, "pages_removed": 0}

    for pair in pairs:
        left_page = left[pair["left"]] if pair["left"] is not None else None
        right_page = right[pair["right"]] if pair["right"] is not None else None
        diff = _word_diff(left_page, right_page)

        state = pair["state"]
        if state == "same" and (diff["added_words"] or diff["removed_words"]):
            state = "changed"          # same position, different words

        regions = []
        if visual and state in ("same", "changed"):
            regions = _visual_diff(left_page, right_page)
            if regions and state == "same":
                state = "changed"

        if state == "added":
            totals["pages_added"] += 1
        elif state == "removed":
            totals["pages_removed"] += 1
        elif state != "same":
            totals["pages_changed"] += 1
        totals["added_words"] += diff["added_words"]
        totals["removed_words"] += diff["removed_words"]

        pages.append({
            "left": pair["left"],
            "right": pair["right"],
            "state": state,
            "added_rects": diff["added_rects"],
            "removed_rects": diff["removed_rects"],
            "visual_rects": regions,
            "added_words": diff["added_words"],
            "removed_words": diff["removed_words"],
            "changes": diff["changes"][:40],
        })

    totals["identical"] = not any(p["state"] != "same" for p in pages)
    return {"pages": pages, "summary": totals}


ADD_COLOUR = (0.36, 0.78, 0.52)
REMOVE_COLOUR = (0.95, 0.52, 0.48)
MOVED_COLOUR = (0.45, 0.62, 0.95)


def mark_up(right: fitz.Document, result: dict, include_visual: bool = True) -> dict:
    """Highlight the changes on the newer document, in place.

    Additions are marked on the page they appear. Deletions have no position in
    the newer document, so they are recorded as a note at the top of the page
    rather than silently dropped.
    """
    marked = 0
    for page_result in result["pages"]:
        index = page_result["right"]
        if index is None:
            continue
        page = right[index]

        for rect in page_result["added_rects"]:
            annot = page.add_highlight_annot(fitz.Rect(rect))
            annot.set_colors(stroke=ADD_COLOUR)
            annot.set_info(title="Compare", content="Added")
            annot.update()
            marked += 1

        if include_visual:
            for rect in page_result["visual_rects"]:
                annot = page.add_rect_annot(fitz.Rect(rect))
                annot.set_colors(stroke=MOVED_COLOUR)
                annot.set_border(width=1)
                annot.set_opacity(0.55)
                annot.set_info(title="Compare", content="Changed appearance")
                annot.update()
                marked += 1

        removed = page_result["removed_words"]
        if removed:
            note = page.add_text_annot(
                fitz.Point(page.rect.x1 - 28, page.rect.y0 + 22),
                "%d word%s removed here:\n\n%s" % (
                    removed, "" if removed == 1 else "s",
                    "\n".join("- " + c["before"] for c in page_result["changes"]
                              if c["before"])[:900]),
                icon="Comment")
            note.set_colors(stroke=REMOVE_COLOUR)
            note.set_info(title="Compare", content=note.info.get("content", ""))
            note.update()
            marked += 1

    return {"marked": marked}
