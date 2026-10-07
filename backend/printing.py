"""Printing a document.

Pages are rendered here and sent straight to the printer, rather than handing
the file to whatever else is installed. Three reasons that matters: it prints
what is on screen including edits that have not been saved yet, it does not
need another PDF application to exist, and it cannot hand the job back to
Platen PDF itself when Platen PDF is the default handler.

Everything is scaled to fit inside the printer's own printable area and
centred. Printers cannot reach the very edge of the paper, so a page sent at
its exact size comes back with a strip missing down one side.
"""
from __future__ import annotations

import pymupdf as fitz

from .session import PdfError

# GetDeviceCaps indices. Named because the numbers alone say nothing.
HORZRES, VERTRES = 8, 10            # printable area, in device pixels
LOGPIXELSX, LOGPIXELSY = 88, 90     # dots per inch
PHYSICALOFFSETX, PHYSICALOFFSETY = 112, 113

# Above roughly 300 the file grows fast and the page looks no better; below
# about 150 text edges start to show.
DEFAULT_DPI = 200
MAX_DPI = 600


# Ports that mean "there is no paper, ask where to put the file". Microsoft
# Print to PDF is the common one and is the default printer on a lot of
# machines, so this is not an edge case.
FILE_PORTS = ("PORTPROMPT:", "FILE:")


def printers() -> dict:
    """Every printer this machine can reach, and which one is the default.

    `to_file` lists the ones that write a file instead of printing, because
    those need somewhere to write before the job can start.
    """
    try:
        import win32print
    except ImportError as exc:        # pragma: no cover - Windows only
        raise PdfError("Printing needs the Windows printing support.") from exc
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    names: list[str] = []
    to_file: list[str] = []
    try:
        for entry in win32print.EnumPrinters(flags, None, 2):
            names.append(entry["pPrinterName"])
            if (entry.get("pPortName") or "").upper() in FILE_PORTS:
                to_file.append(entry["pPrinterName"])
    except Exception:
        # Level 2 can fail on a locked-down machine; the names still work.
        names = [entry[2] for entry in win32print.EnumPrinters(flags)]
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:
        default = names[0] if names else None
    return {"printers": names, "default": default, "to_file": to_file}


def needs_destination(printer: str) -> bool:
    """Whether this printer writes a file and so needs somewhere to write."""
    try:
        return printer in printers().get("to_file", [])
    except PdfError:
        return False


def parse_range(text: str, page_count: int) -> list[int]:
    """'1-3, 7' -> [0, 1, 2, 6]. Empty means the whole document.

    People type ranges the way they read them, from one, and get them wrong in
    predictable ways: backwards, out of bounds, with stray separators. Each of
    those is clamped or skipped rather than refused, because refusing a print
    over a typo is worse than printing the pages that made sense.
    """
    if not text or not text.strip():
        return list(range(page_count))
    wanted: list[int] = []
    for chunk in text.replace(";", ",").split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if "-" in piece:
            first, _, last = piece.partition("-")
            try:
                start = int(first.strip() or 1)
                end = int(last.strip() or page_count)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            for number in range(start, end + 1):
                if 1 <= number <= page_count:
                    wanted.append(number - 1)
        else:
            try:
                number = int(piece)
            except ValueError:
                continue
            if 1 <= number <= page_count:
                wanted.append(number - 1)
    # Keep the order asked for, drop repeats.
    seen = set()
    return [p for p in wanted if not (p in seen or seen.add(p))]


def fit_box(page_w: float, page_h: float, area_w: int, area_h: int) -> tuple:
    """Centre the page inside the printable area without distorting it.

    Returns (left, top, right, bottom) in device pixels.
    """
    if page_w <= 0 or page_h <= 0 or area_w <= 0 or area_h <= 0:
        return (0, 0, max(area_w, 1), max(area_h, 1))
    scale = min(area_w / page_w, area_h / page_h)
    width = int(round(page_w * scale))
    height = int(round(page_h * scale))
    left = (area_w - width) // 2
    top = (area_h - height) // 2
    return (left, top, left + width, top + height)


def print_document(doc: fitz.Document, printer: str | None = None,
                   pages: str = "", copies: int = 1,
                   title: str = "Platen PDF", dpi: int = DEFAULT_DPI,
                   destination: str | None = None) -> dict:
    """Send the document to a printer. Returns what was actually sent."""
    try:
        import win32print
        import win32ui
        from PIL import Image, ImageWin
    except ImportError as exc:        # pragma: no cover - Windows only
        raise PdfError("Printing needs the Windows printing support.") from exc

    if doc is None or doc.page_count == 0:
        raise PdfError("There is nothing to print.")

    chosen = printer or printers()["default"]
    if not chosen:
        raise PdfError("No printer is set up on this PC.")

    wanted = parse_range(pages, doc.page_count)
    if not wanted:
        raise PdfError("That page range does not include any pages of this document.")

    copies = max(1, min(int(copies or 1), 99))
    dpi = max(72, min(int(dpi or DEFAULT_DPI), MAX_DPI))

    context = win32ui.CreateDC()
    try:
        context.CreatePrinterDC(chosen)
    except Exception as exc:
        raise PdfError("Could not reach the printer %r. Is it still "
                       "installed?" % chosen) from exc

    sent = 0
    try:
        area_w = context.GetDeviceCaps(HORZRES)
        area_h = context.GetDeviceCaps(VERTRES)
        # A file-port printer refuses to start without somewhere to write,
        # with nothing but "StartDoc failed" to explain itself.
        if destination:
            context.StartDoc(title, destination)
        else:
            context.StartDoc(title)
        try:
            for _ in range(copies):
                for index in wanted:
                    page = doc[index]
                    # Render at the printer's resolution rather than the
                    # screen's, or everything prints at monitor quality.
                    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    image = Image.frombytes(
                        "RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    box = fit_box(pixmap.width, pixmap.height, area_w, area_h)
                    context.StartPage()
                    ImageWin.Dib(image).draw(context.GetHandleOutput(), box)
                    context.EndPage()
                    sent += 1
        finally:
            context.EndDoc()
    except PdfError:
        raise
    except Exception as exc:
        if "StartDoc" in str(exc) and not destination:
            raise PdfError(
                "%s writes to a file rather than paper, so it needs somewhere "
                "to save. Choose a file name and try again." % chosen) from exc
        raise PdfError("The printer refused the job: %s" % exc) from exc
    finally:
        try:
            context.DeleteDC()
        except Exception:
            pass

    return {"printer": chosen, "pages": len(wanted), "copies": copies,
            "sheets": sent, "file": destination}
