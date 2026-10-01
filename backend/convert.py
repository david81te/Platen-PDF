"""Conversion in and out of PDF, OCR and file-size optimisation."""
from __future__ import annotations

import os
import re
import shutil
import tempfile

import pymupdf as fitz

from .session import PdfError

PT_TO_EMU = 12700

# Remove text but keep rules, shading and pictures -- used to build a slide
# background that still shows tables while the words become editable.
_KEEP_GRAPHICS = {}
for _name, _attr in (("images", "PDF_REDACT_IMAGE_NONE"),
                     ("graphics", "PDF_REDACT_LINE_ART_NONE"),
                     ("text", "PDF_REDACT_TEXT_REMOVE")):
    _value = getattr(fitz, _attr, None)
    if _value is not None:
        _KEEP_GRAPHICS[_name] = _value
OFFICE_INPUTS = (".doc", ".docx", ".rtf", ".odt", ".txt",
                 ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".odp", ".ods")
IMAGE_INPUTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


def _temp_pdf(doc: fitz.Document) -> str:
    """Write the document's current state somewhere converters can read it."""
    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    doc.save(path, garbage=4, deflate=True)
    return path


# ---- PDF out -------------------------------------------------------------

def to_docx(doc: fitz.Document, path: str, first: int = 0,
            last: int | None = None) -> dict:
    """Convert to Word, preserving layout as closely as pdf2docx manages."""
    try:
        from pdf2docx import Converter
    except ImportError as exc:
        raise PdfError("The Word converter is not installed.") from exc
    source = _temp_pdf(doc)
    try:
        converter = Converter(source)
        try:
            converter.convert(path, start=first, end=last)
        finally:
            converter.close()
    except Exception as exc:
        raise PdfError("Word conversion failed: " + str(exc)) from exc
    finally:
        try:
            os.remove(source)
        except OSError:
            pass
    return {"path": path}


CURRENCY = {"$": '"$"#,##0.00', "£": '"£"#,##0.00',
            "€": '"€"#,##0.00', "¥": '"¥"#,##0'}
_NUMBER = re.compile(r"^-?[\d,]*\d(?:\.\d+)?$")


def _coerce(raw):
    """Turn a cell of PDF text into a real value plus a number format.

    A column of prices exported as text cannot be summed, which is most of the
    reason to open it in Excel at all. Anything not confidently numeric is left
    as text rather than guessed at.
    """
    if raw is None:
        return "", None
    text = str(raw).strip()
    if not text:
        return "", None

    negative = False
    body = text
    if body.startswith("(") and body.endswith(")"):    # (1,234) accounting style
        negative, body = True, body[1:-1].strip()

    fmt = None
    symbol = body[:1]
    if symbol in CURRENCY:
        fmt = CURRENCY[symbol]
        body = body[1:].strip()
    elif body.endswith("%"):
        fmt = "0.0%"
        body = body[:-1].strip()

    if not _NUMBER.match(body):
        return text, None
    try:
        value = float(body.replace(",", ""))
    except ValueError:
        return text, None
    if negative:
        value = -value
    if fmt == "0.0%":
        value /= 100.0
    if fmt is None:
        fmt = "#,##0.00" if "." in body else "#,##0"
    if float(value).is_integer() and fmt == "#,##0.00":
        fmt = "#,##0"
    return value, fmt


def _looks_like_header(row) -> bool:
    """A header row is text in every populated cell."""
    filled = [c for c in row if str(c or "").strip()]
    if not filled:
        return False
    return all(_coerce(c)[1] is None for c in filled)


_BAD_SHEET_CHARS = re.compile("[" + re.escape("[]:*?/" + chr(92)) + "]")


def _sheet_name(book, base: str) -> str:
    """Excel sheet names: 31 characters, no brackets or slashes, and unique."""
    clean = re.sub(_BAD_SHEET_CHARS, " ", base).strip() or "Sheet"
    clean = clean[:31]
    if clean not in book.sheetnames:
        return clean
    stem = clean[:27]
    for n in range(2, 200):
        candidate = "%s (%d)" % (stem, n)
        if candidate not in book.sheetnames:
            return candidate
    return clean[:28] + "~"


def _table_caption(page, table) -> str:
    """The line of text just above a table, if there is one -- it names it."""
    try:
        box = fitz.Rect(table.bbox)
    except Exception:
        return ""
    best, best_gap = "", 60
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        b = fitz.Rect(block["bbox"])
        gap = box.y0 - b.y1
        if 0 <= gap < best_gap and b.x1 > box.x0 and b.x0 < box.x1:
            text = " ".join("".join(s.get("text", "") for s in line.get("spans", []))
                            for line in block.get("lines", []))
            text = " ".join(text.split())
            if 0 < len(text) <= 60:
                best, best_gap = text, gap
    return best


def to_xlsx(doc: fitz.Document, path: str, merge_continuations: bool = True) -> dict:
    """Export detected tables to Excel, one sheet per table.

    Numbers, currency and percentages become real numeric cells so they can be
    summed and charted. A table continuing on the next page under the same
    headings is appended to the same sheet rather than split across two.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise PdfError("The Excel converter is not installed.") from exc

    book = Workbook()
    book.remove(book.active)
    tables = 0
    sheets = []
    last = None          # (sheet, header tuple, column count)

    for index in range(doc.page_count):
        page = doc[index]
        try:
            found = list(page.find_tables())
        except Exception:
            found = []
        for number, table in enumerate(found, start=1):
            rows = [r for r in table.extract() if any(str(c or "").strip() for c in r)]
            if not rows:
                continue
            header = tuple(str(c or "").strip() for c in rows[0])
            has_header = _looks_like_header(rows[0])

            sheet = None
            if (merge_continuations and last and has_header
                    and last[1] == header and last[2] == len(rows[0])):
                sheet = last[0]          # same table, continued on this page
                rows = rows[1:]
            if sheet is None:
                caption = _table_caption(page, table)
                base = caption or "Page %d table %d" % (index + 1, number)
                sheet = book.create_sheet(_sheet_name(book, base))
                sheets.append(sheet.title)
                tables += 1
                last = (sheet, header if has_header else None, len(rows[0]))

            for row in rows:
                values, formats = [], []
                for cell in row:
                    value, fmt = _coerce(cell)
                    values.append(value)
                    formats.append(fmt)
                sheet.append(values)
                for column, fmt in enumerate(formats, start=1):
                    if fmt:
                        sheet.cell(row=sheet.max_row, column=column).number_format = fmt

    if not tables:
        # Nothing grid-like: give them the page text rather than an empty file.
        sheet = book.create_sheet("Text")
        sheet.append(["Page", "Line"])
        for index in range(doc.page_count):
            for line in doc[index].get_text().splitlines():
                if line.strip():
                    sheet.append([index + 1, line.rstrip()])
        sheets.append("Text")

    heading = Font(bold=True, color="1F2937")
    shade = PatternFill("solid", fgColor="EEF2F8")
    for sheet in book:
        if sheet.max_row > 1:
            for cell in sheet[1]:
                cell.font = heading
                cell.fill = shade
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            longest = max((len(str(c.value if c.value is not None else "")))
                          for c in column)
            letter = get_column_letter(column[0].column)
            sheet.column_dimensions[letter].width = max(9, min(52, longest + 3))

    book.save(path)
    return {"path": path, "tables": tables, "sheets": sheets}


def _pptx_font(pdf_font: str) -> str:
    """A font family PowerPoint can resolve, from a PDF font name."""
    from . import fonts as fontmod
    name = fontmod.normalize(pdf_font or "")
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(MT|PS|PSMT|Std|Pro)$", "", name).strip()
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)   # TimesNewRoman -> Times New Roman
    return spaced or "Calibri"


def _graphics_only(doc: fitz.Document, page_no: int, zoom: float):
    """Render a page with its text removed, keeping rules, shading and images.

    This becomes the slide background, so tables, logos and boxes survive while
    the words on top stay editable. Rendering the whole page instead would bake
    the text into a picture; dropping the graphics would lose every table
    border.
    """
    scratch = fitz.open()
    scratch.insert_pdf(doc, from_page=page_no, to_page=page_no)
    page = scratch[0]
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 0:
            page.add_redact_annot(fitz.Rect(block["bbox"]))
    try:
        page.apply_redactions(**_KEEP_GRAPHICS)
    except Exception:
        pass
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    scratch.close()
    return pix


def _deck_size(doc: fitz.Document) -> tuple[float, float]:
    """PowerPoint allows one slide size per deck, so pick the commonest page."""
    tally: dict[tuple[int, int], int] = {}
    for index in range(doc.page_count):
        rect = doc[index].rect
        key = (round(rect.width), round(rect.height))
        tally[key] = tally.get(key, 0) + 1
    best = max(tally.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))[0]
    return float(best[0]), float(best[1])


def to_pptx(doc: fitz.Document, path: str, mode: str = "editable",
            dpi: int = 150) -> dict:
    """Export to PowerPoint.

    editable - page graphics as the slide background, with real text boxes on
               top, so the wording can be changed but tables and rules survive
    image    - each page as one flat picture: exact, nothing editable
    text     - text boxes only, on a blank slide
    """
    try:
        from pptx import Presentation
        from pptx.util import Emu, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
    except ImportError as exc:
        raise PdfError("The PowerPoint converter is not installed.") from exc

    deck = Presentation()
    deck_w, deck_h = _deck_size(doc)
    deck.slide_width = Emu(int(deck_w * PT_TO_EMU))
    deck.slide_height = Emu(int(deck_h * PT_TO_EMU))
    blank = deck.slide_layouts[6]
    scratch = tempfile.mkdtemp(prefix="pdf2pptx_")
    zoom = max(72, min(int(dpi), 400)) / 72.0
    scaled_pages = 0

    try:
        for index in range(doc.page_count):
            page = doc[index]
            rect = page.rect
            slide = deck.slides.add_slide(blank)

            # Pages that differ from the deck size are fitted and centred
            # rather than stretched out of shape.
            scale = min(deck_w / rect.width, deck_h / rect.height)
            if abs(scale - 1.0) > 0.001:
                scaled_pages += 1
            off_x = (deck_w - rect.width * scale) / 2
            off_y = (deck_h - rect.height * scale) / 2

            def emu(value, offset=0.0):
                return Emu(int((offset + value * scale) * PT_TO_EMU))

            if mode != "text":
                pix = (page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                       if mode == "image" else _graphics_only(doc, index, zoom))
                image_path = os.path.join(scratch, "bg%03d.png" % index)
                pix.save(image_path)
                slide.shapes.add_picture(
                    image_path, emu(0, off_x), emu(0, off_y),
                    Emu(int(rect.width * scale * PT_TO_EMU)),
                    Emu(int(rect.height * scale * PT_TO_EMU)))
            if mode == "image":
                continue

            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines = [ln for ln in block.get("lines", []) if ln.get("spans")]
                if not lines:
                    continue
                bbox = fitz.Rect(block["bbox"])
                box = slide.shapes.add_textbox(
                    emu(bbox.x0 - 1, off_x), emu(bbox.y0 - 1, off_y),
                    emu(max(bbox.width + 6, 12)), emu(max(bbox.height + 4, 10)))
                frame = box.text_frame
                frame.word_wrap = False
                frame.margin_left = frame.margin_right = 0
                frame.margin_top = frame.margin_bottom = 0

                for line_no, line in enumerate(lines):
                    para = frame.paragraphs[0] if line_no == 0 else frame.add_paragraph()
                    para.alignment = PP_ALIGN.LEFT
                    for span in line["spans"]:
                        text = span.get("text", "")
                        if not text:
                            continue
                        run = para.add_run()
                        run.text = text
                        flags = span.get("flags", 0)
                        run.font.size = Pt(max(5, round(span.get("size", 11) * scale, 1)))
                        run.font.bold = bool(flags & 16)
                        run.font.italic = bool(flags & 2)
                        run.font.name = _pptx_font(span.get("font", ""))
                        red, green, blue = fitz.sRGB_to_pdf(span.get("color", 0))
                        run.font.color.rgb = RGBColor(int(red * 255), int(green * 255),
                                                      int(blue * 255))
        deck.save(path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"path": path, "slides": doc.page_count, "mode": mode,
            "resized_pages": scaled_pages}


def to_images(doc: fitz.Document, out_dir: str, dpi: int = 200,
              fmt: str = "png", pages: list[int] | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    fmt = fmt.lower().strip(".")
    if fmt not in ("png", "jpg", "jpeg"):
        raise PdfError("Choose PNG or JPG.")
    zoom = max(36, min(int(dpi), 600)) / 72.0
    targets = pages if pages else range(doc.page_count)
    written = []
    for index in targets:
        pix = doc[index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        path = os.path.join(out_dir, "page_%03d.%s" % (index + 1, fmt))
        pix.save(path, jpg_quality=88) if fmt in ("jpg", "jpeg") else pix.save(path)
        written.append(path)
    return {"files": written, "count": len(written)}


def to_text(doc: fitz.Document, path: str) -> dict:
    chunks = []
    for index in range(doc.page_count):
        chunks.append(doc[index].get_text())
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(chunks))
    return {"path": path}


# ---- PDF in --------------------------------------------------------------

def _word_export(paths: list[str], out_dir: str) -> list[str]:
    """Convert Office documents with the installed Office apps (best fidelity)."""
    try:
        import win32com.client as win32
    except ImportError as exc:
        raise PdfError("Microsoft Office automation is unavailable.") from exc

    produced = []
    apps: dict[str, object] = {}

    def app(name: str):
        if name not in apps:
            # DispatchEx, never Dispatch. Dispatch hands back the copy of Word
            # or Excel the user already has open, and everything below is then
            # done to their live session: their workbook hidden by
            # Visible = False, their application shut down by the Quit() in the
            # finally, with whatever they had unsaved. DispatchEx always starts
            # a private instance, so a conversion cannot touch their work.
            handle = win32.DispatchEx(name + ".Application")
            try:
                handle.Visible = False
            except Exception:
                pass
            apps[name] = handle
        return apps[name]

    try:
        for source in paths:
            ext = os.path.splitext(source)[1].lower()
            target = os.path.join(out_dir, os.path.splitext(os.path.basename(source))[0] + ".pdf")
            if ext in (".doc", ".docx", ".rtf", ".odt", ".txt"):
                document = app("Word").Documents.Open(os.path.abspath(source))
                try:
                    document.ExportAsFixedFormat(os.path.abspath(target), 17)
                finally:
                    document.Close(False)
            elif ext in (".xls", ".xlsx", ".csv", ".ods"):
                book = app("Excel").Workbooks.Open(os.path.abspath(source))
                try:
                    book.ExportAsFixedFormat(0, os.path.abspath(target))
                finally:
                    book.Close(False)
            elif ext in (".ppt", ".pptx", ".odp"):
                deck = app("PowerPoint").Presentations.Open(
                    os.path.abspath(source), WithWindow=False)
                try:
                    deck.SaveAs(os.path.abspath(target), 32)
                finally:
                    deck.Close()
            else:
                continue
            produced.append(target)
    finally:
        for handle in apps.values():
            try:
                handle.Quit()
            except Exception:
                pass
    return produced


def _soffice_export(paths: list[str], out_dir: str) -> list[str]:
    binary = shutil.which("soffice") or shutil.which("soffice.exe")
    if not binary:
        raise PdfError("Install Microsoft Office or LibreOffice to convert these files.")
    import subprocess
    produced = []
    for source in paths:
        subprocess.run([binary, "--headless", "--convert-to", "pdf",
                        "--outdir", out_dir, source], check=True,
                       capture_output=True, timeout=180)
        candidate = os.path.join(out_dir, os.path.splitext(os.path.basename(source))[0] + ".pdf")
        if os.path.isfile(candidate):
            produced.append(candidate)
    return produced


def from_files(paths: list[str]) -> fitz.Document:
    """Build a PDF from Office documents, images and existing PDFs."""
    if not paths:
        raise PdfError("No files selected.")
    scratch = tempfile.mkdtemp(prefix="topdf_")
    out = fitz.open()
    try:
        office = [p for p in paths if os.path.splitext(p)[1].lower() in OFFICE_INPUTS]
        converted: dict[str, str] = {}
        if office:
            # Office raises com_error, not PdfError, when it is not installed,
            # so catch broadly or the LibreOffice fallback is never reached.
            produced: list[str] = []
            for attempt in (_word_export, _soffice_export):
                try:
                    produced = attempt(office, scratch)
                except Exception:
                    produced = []
                if produced:
                    break
            if not produced:
                raise PdfError(
                    "Opening Word, Excel and PowerPoint files needs Microsoft "
                    "Office or LibreOffice installed on this PC. PDFs and "
                    "images work without either.")
            for source, result in zip(office, produced):
                converted[source] = result

        for source in paths:
            ext = os.path.splitext(source)[1].lower()
            if ext in IMAGE_INPUTS:
                image = fitz.open(source)
                pdf_bytes = image.convert_to_pdf()
                image.close()
                page = fitz.open("pdf", pdf_bytes)
                out.insert_pdf(page)
                page.close()
            elif ext == ".pdf":
                other = fitz.open(source)
                out.insert_pdf(other)
                other.close()
            elif source in converted:
                other = fitz.open(converted[source])
                out.insert_pdf(other)
                other.close()
        if out.page_count == 0:
            raise PdfError("None of those files could be converted.")
        return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---- OCR -----------------------------------------------------------------
#
# The OCR engine ships inside the app: RapidOCR runs ONNX models through
# onnxruntime, so there is nothing for anyone to install. We keep the original
# page untouched and lay invisible text over it at the recognised positions,
# which makes a scan searchable and selectable without altering how it looks.

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise PdfError("The OCR engine is missing from this build.") from exc
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_status() -> dict:
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return {"available": True, "engine": "RapidOCR (built in)", "needs_install": False}
    except ImportError:
        return {"available": False, "engine": None, "needs_install": True}



def _quad_bounds(box) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def ocr(doc: fitz.Document, language: str = "eng", dpi: int = 220,
        pages: list[int] | None = None, force: bool = False) -> dict:
    """Add a searchable invisible text layer to scanned pages."""
    import numpy as np

    engine = _engine()
    targets = list(pages) if pages else list(range(doc.page_count))
    zoom = max(72, min(int(dpi), 400)) / 72.0
    helv = fitz.Font(fontname="helv")

    done = 0
    skipped = 0
    words = 0
    for index in targets:
        page = doc[index]
        if not force and page.get_text().strip():
            skipped += 1
            continue
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        try:
            found, _ = engine(array)
        except Exception as exc:
            raise PdfError("OCR failed on page %d: %s" % (index + 1, exc)) from exc
        if not found:
            continue

        page.insert_font(fontname="helv")
        for box, text, score in found:
            text = (text or "").strip()
            if not text or float(score) < 0.3:
                continue
            x0, y0, x1, y1 = _quad_bounds(box)
            # Recognised coordinates are in rendered pixels; the page is points.
            x0, y0, x1, y1 = x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom
            width = max(1.0, x1 - x0)
            height = max(1.0, y1 - y0)
            unit = helv.text_length(text, fontsize=1.0) or 1.0
            size = max(1.0, min(width / unit, height * 1.2))
            page.insert_text(
                fitz.Point(x0, y1 - height * 0.18),
                text,
                fontname="helv",
                fontsize=size,
                render_mode=3,  # invisible: selectable and searchable only
            )
            words += 1
        done += 1

    if not done and skipped:
        raise PdfError(
            "Every page already contains real text. Use Force re-recognise "
            "if you want to OCR them anyway.")
    return {"pages": done, "skipped": skipped, "blocks": words}


# ---- optimisation --------------------------------------------------------

LEVELS = {"low": (0, 92), "medium": (1600, 78), "high": (1100, 60)}


def compress(doc: fitz.Document, path: str, level: str = "medium") -> dict:
    """Shrink the file by downsampling images and subsetting fonts."""
    if level not in LEVELS:
        level = "medium"
    max_side, quality = LEVELS[level]
    shrunk = 0
    if max_side:
        from PIL import Image
        import io
        seen = set()
        for index in range(doc.page_count):
            for info in doc[index].get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    raw = doc.extract_image(xref)
                    image = Image.open(io.BytesIO(raw["image"]))
                    if max(image.size) <= max_side:
                        continue
                    ratio = max_side / max(image.size)
                    image = image.convert("RGB").resize(
                        (max(1, int(image.width * ratio)),
                         max(1, int(image.height * ratio))), Image.LANCZOS)
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=quality, optimize=True)
                    doc.replace_image(xref, stream=buffer.getvalue())
                    shrunk += 1
                except Exception:
                    continue
    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(path, garbage=4, deflate=True, deflate_images=True,
             deflate_fonts=True, clean=True)
    return {"path": path, "images_resampled": shrunk,
            "size": os.path.getsize(path)}
