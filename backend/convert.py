"""Conversion in and out of PDF, OCR and file-size optimisation."""
from __future__ import annotations

import os
import shutil
import tempfile

import pymupdf as fitz

from .session import PdfError

PT_TO_EMU = 12700
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


def to_xlsx(doc: fitz.Document, path: str) -> dict:
    """Export detected tables to Excel, one sheet per table."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:
        raise PdfError("The Excel converter is not installed.") from exc

    book = Workbook()
    book.remove(book.active)
    tables = 0
    for index in range(doc.page_count):
        page = doc[index]
        try:
            found = list(page.find_tables())
        except Exception:
            found = []
        for number, table in enumerate(found, start=1):
            rows = table.extract()
            if not rows:
                continue
            tables += 1
            sheet = book.create_sheet("P%d_T%d" % (index + 1, number))
            for row in rows:
                sheet.append(["" if cell is None else str(cell) for cell in row])
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for column in sheet.columns:
                longest = max((len(str(c.value or "")) for c in column), default=8)
                sheet.column_dimensions[column[0].column_letter].width = min(60, longest + 2)
    if not tables:
        # No detectable grid: fall back to page text so the export is never empty.
        sheet = book.create_sheet("Text")
        sheet.append(["Page", "Line"])
        for index in range(doc.page_count):
            for line in doc[index].get_text().splitlines():
                if line.strip():
                    sheet.append([index + 1, line])
    book.save(path)
    return {"path": path, "tables": tables}


def to_pptx(doc: fitz.Document, path: str, mode: str = "editable",
            dpi: int = 150) -> dict:
    """Export to PowerPoint.

    editable - text becomes real text boxes and images are placed separately
    image    - each page is rendered as a full-slide picture (exact but flat)
    """
    try:
        from pptx import Presentation
        from pptx.util import Emu, Pt
        from pptx.dml.color import RGBColor
    except ImportError as exc:
        raise PdfError("The PowerPoint converter is not installed.") from exc

    deck = Presentation()
    first = doc[0].rect
    deck.slide_width = Emu(int(first.width * PT_TO_EMU))
    deck.slide_height = Emu(int(first.height * PT_TO_EMU))
    blank = deck.slide_layouts[6]
    scratch = tempfile.mkdtemp(prefix="pdf2pptx_")

    try:
        for index in range(doc.page_count):
            page = doc[index]
            slide = deck.slides.add_slide(blank)
            if mode == "image":
                zoom = dpi / 72.0
                image_path = os.path.join(scratch, "page%d.png" % index)
                page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(image_path)
                slide.shapes.add_picture(image_path, 0, 0,
                                         width=deck.slide_width,
                                         height=deck.slide_height)
                continue

            for number, info in enumerate(page.get_images(full=True)):
                try:
                    pix = fitz.Pixmap(doc, info[0])
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    image_path = os.path.join(scratch, "p%d_i%d.png" % (index, number))
                    pix.save(image_path)
                    for box in page.get_image_rects(info[0]):
                        slide.shapes.add_picture(
                            image_path, Emu(int(box.x0 * PT_TO_EMU)),
                            Emu(int(box.y0 * PT_TO_EMU)),
                            Emu(int(box.width * PT_TO_EMU)),
                            Emu(int(box.height * PT_TO_EMU)))
                except Exception:
                    continue

            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                bbox = fitz.Rect(block["bbox"])
                box = slide.shapes.add_textbox(
                    Emu(int(bbox.x0 * PT_TO_EMU)), Emu(int(bbox.y0 * PT_TO_EMU)),
                    Emu(int(max(bbox.width, 10) * PT_TO_EMU)),
                    Emu(int(max(bbox.height, 10) * PT_TO_EMU)))
                frame = box.text_frame
                frame.word_wrap = True
                first_line = True
                for line in block.get("lines", []):
                    text = "".join(s.get("text", "") for s in line.get("spans", []))
                    if not text.strip():
                        continue
                    para = frame.paragraphs[0] if first_line else frame.add_paragraph()
                    first_line = False
                    run = para.add_run()
                    run.text = text
                    spans = line.get("spans", [])
                    if spans:
                        span = spans[0]
                        run.font.size = Pt(max(6, span.get("size", 11)))
                        run.font.bold = bool(span.get("flags", 0) & 16)
                        run.font.italic = bool(span.get("flags", 0) & 2)
                        red, green, blue = fitz.sRGB_to_pdf(span.get("color", 0))
                        run.font.color.rgb = RGBColor(int(red * 255), int(green * 255),
                                                      int(blue * 255))
        deck.save(path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"path": path, "slides": doc.page_count}


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
            handle = win32.Dispatch(name + ".Application")
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
            try:
                produced = _word_export(office, scratch)
            except PdfError:
                produced = _soffice_export(office, scratch)
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
