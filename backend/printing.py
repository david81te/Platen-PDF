"""Printing a document.

Pages are rendered here and sent straight to the printer, rather than handing
the file to whatever else is installed. Three reasons that matters: it prints
what is on screen including edits that have not been saved yet, it does not
need another PDF application to exist, and it cannot hand the job back to
Platen PDF itself when Platen PDF is the default handler.

Two jobs are split between us and the driver. Which pages go on which sheet,
and in what order - odd or even only, reversed, several pages to a sheet,
collated or not - is decided here, because it is the same on every printer and
because we are rendering the pages anyway. Orientation, double-sided and
colour are set on the driver through its DEVMODE, because only the driver
knows how to fold paper.

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

# DeviceCapabilities indices, for asking a printer what it can actually do
# rather than offering a choice that silently does nothing.
DC_BINS, DC_DUPLEX, DC_ORIENTATION = 6, 7, 17
DC_COPIES, DC_COLLATE, DC_COLORDEVICE = 18, 22, 32

# DEVMODE values. win32con has these, but importing it here would make the
# module unimportable on a machine without pywin32, and the tests need the
# page-selection arithmetic without a printer anywhere near them.
DM_ORIENTATION, DM_COPIES, DM_COLOR = 0x1, 0x100, 0x800
DM_DUPLEX, DM_COLLATE = 0x1000, 0x8000
DMORIENT_PORTRAIT, DMORIENT_LANDSCAPE = 1, 2
DMDUP_SIMPLEX, DMDUP_VERTICAL, DMDUP_HORIZONTAL = 1, 2, 3
DMCOLOR_MONOCHROME, DMCOLOR_COLOR = 1, 2
DMCOLLATE_FALSE, DMCOLLATE_TRUE = 0, 1

# Above roughly 300 the file grows fast and the page looks no better; below
# about 150 text edges start to show.
DEFAULT_DPI = 200
MAX_DPI = 600

# Ports that mean "there is no paper, ask where to put the file". Microsoft
# Print to PDF is the common one and is the default printer on a lot of
# machines, so this is not an edge case.
FILE_PORTS = ("PORTPROMPT:", "FILE:")

# How many pages fit on a sheet, and the grid each one uses. The grid is
# given as (across, down) for a portrait sheet; a landscape sheet turns it
# the other way up, so two pages sit side by side rather than stacked.
SHEET_GRIDS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 9: (3, 3), 16: (4, 4)}

SUBSETS = ("all", "odd", "even")


def capabilities(name: str, port: str = "") -> dict:
    """What this printer can do. Anything unknown is reported as absent.

    A choice the printer cannot honour is worse than no choice at all: the
    person ticks it, the job prints wrong, and nothing says why.
    """
    answer = {"duplex": False, "collate": False, "colour": False, "max_copies": 1}
    try:
        import win32print
    except ImportError:               # pragma: no cover - Windows only
        return answer
    for key, index in (("duplex", DC_DUPLEX), ("collate", DC_COLLATE),
                       ("colour", DC_COLORDEVICE)):
        try:
            answer[key] = bool(win32print.DeviceCapabilities(name, port, index))
        except Exception:
            pass
    try:
        answer["max_copies"] = max(1, int(
            win32print.DeviceCapabilities(name, port, DC_COPIES)))
    except Exception:
        pass
    return answer


# Asking Windows for the full printer list takes a couple of seconds on a
# machine with network printers, because it goes and talks to them. That is
# the whole delay between choosing Print and the dialog appearing, so the
# answer is kept for a short while: long enough that opening the dialog twice
# is instant, short enough that a printer added in Settings turns up quickly.
_CACHE: dict = {}
CACHE_SECONDS = 20.0


def printers(refresh: bool = False) -> dict:
    """Every printer this machine can reach, and which one is the default.

    `to_file` lists the ones that write a file instead of printing, because
    those need somewhere to write before the job can start. `can` carries what
    each one supports, so the dialog can offer only the real choices.
    """
    import time
    if not refresh and _CACHE:
        if time.monotonic() - _CACHE.get("at", 0) < CACHE_SECONDS:
            return _CACHE["value"]
    try:
        import win32print
    except ImportError as exc:        # pragma: no cover - Windows only
        raise PdfError("Printing needs the Windows printing support.") from exc
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    names: list[str] = []
    to_file: list[str] = []
    can: dict[str, dict] = {}
    try:
        for entry in win32print.EnumPrinters(flags, None, 2):
            name = entry["pPrinterName"]
            port = entry.get("pPortName") or ""
            names.append(name)
            if port.upper() in FILE_PORTS:
                to_file.append(name)
            can[name] = capabilities(name, port)
    except Exception:
        # Level 2 can fail on a locked-down machine; the names still work.
        names = [entry[2] for entry in win32print.EnumPrinters(flags)]
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:
        default = names[0] if names else None
    answer = {"printers": names, "default": default, "to_file": to_file,
              "can": can, "per_sheet": sorted(SHEET_GRIDS)}
    _CACHE["value"] = answer
    _CACHE["at"] = time.monotonic()
    return answer


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


def apply_subset(indexes: list[int], subset: str = "all") -> list[int]:
    """Odd or even as the person counts them, from page 1, not from zero.

    Odd and even are read off the page numbers printed on the document, not
    off a position in the selection: with pages 2-7 chosen, "odd" means 3, 5
    and 7, which is what someone reprinting one side of a stack expects.
    """
    if subset == "odd":
        return [i for i in indexes if (i + 1) % 2 == 1]
    if subset == "even":
        return [i for i in indexes if (i + 1) % 2 == 0]
    return list(indexes)


def grid_for(per_sheet: int, landscape: bool) -> tuple[int, int]:
    """(across, down) for this many pages on a sheet of this shape."""
    across, down = SHEET_GRIDS.get(int(per_sheet or 1), (1, 1))
    if landscape:
        across, down = down, across
    return across, down


def build_sheets(indexes: list[int], per_sheet: int = 1, reverse: bool = False,
                 copies: int = 1, collate: bool = True) -> list[list[int]]:
    """The exact sequence of sheets to print, each holding one or more pages.

    Reversing happens before grouping, so a reversed two-up job reads the same
    way round as a reversed one-up job rather than shuffling pairs.

    Collated means the whole document, then the whole document again, which is
    what someone stapling three handouts wants. Uncollated means every copy of
    sheet one together, which is what someone feeding a guillotine wants.
    """
    pages = list(indexes)
    if reverse:
        pages.reverse()
    size = max(1, int(per_sheet or 1))
    sheets = [pages[at:at + size] for at in range(0, len(pages), size)]
    copies = max(1, int(copies or 1))
    if copies == 1 or not sheets:
        return sheets
    if collate:
        return sheets * copies
    return [sheet for sheet in sheets for _ in range(copies)]


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


def cell_box(index: int, across: int, down: int, area_w: int, area_h: int,
             gap: int = 0) -> tuple:
    """Where the nth page sits on a sheet carrying `across` x `down` of them.

    Reading order: left to right, then down, which is how a person expects a
    handout to run.
    """
    across = max(1, int(across))
    down = max(1, int(down))
    column = index % across
    row = (index // across) % down
    width = (area_w - gap * (across - 1)) // across
    height = (area_h - gap * (down - 1)) // down
    left = column * (width + gap)
    top = row * (height + gap)
    return left, top, width, height


def _devmode(printer: str, orientation: str, duplex: str, colour: bool,
             collate: bool):
    """A DEVMODE with our choices set, or None to accept the printer's own.

    Only the fields actually being changed are flagged, because a driver reads
    the flags to decide what to pay attention to; setting a value without its
    flag changes nothing and is the usual reason "landscape does not work".
    """
    try:
        import win32print
    except ImportError:               # pragma: no cover - Windows only
        return None
    try:
        handle = win32print.OpenPrinter(printer)
    except Exception:
        return None
    try:
        info = win32print.GetPrinter(handle, 2)
    except Exception:
        return None
    finally:
        try:
            win32print.ClosePrinter(handle)
        except Exception:
            pass

    mode = info.get("pDevMode")
    if mode is None:
        return None
    fields = 0
    if orientation in ("portrait", "landscape"):
        mode.Orientation = (DMORIENT_LANDSCAPE if orientation == "landscape"
                            else DMORIENT_PORTRAIT)
        fields |= DM_ORIENTATION
    if duplex in ("long", "short", "none"):
        mode.Duplex = {"long": DMDUP_VERTICAL, "short": DMDUP_HORIZONTAL,
                       "none": DMDUP_SIMPLEX}[duplex]
        fields |= DM_DUPLEX
    mode.Color = DMCOLOR_COLOR if colour else DMCOLOR_MONOCHROME
    fields |= DM_COLOR
    # We lay the copies out ourselves, so the driver must print the sequence
    # once; asking it for copies as well would multiply them.
    mode.Copies = 1
    mode.Collate = DMCOLLATE_TRUE if collate else DMCOLLATE_FALSE
    fields |= DM_COPIES | DM_COLLATE
    mode.Fields = mode.Fields | fields
    return mode


def _context(printer: str, orientation: str, duplex: str, colour: bool,
             collate: bool):
    """A device context for this printer, carrying our settings if it can."""
    import win32ui
    mode = _devmode(printer, orientation, duplex, colour, collate)
    if mode is not None:
        try:
            import win32gui
            return win32ui.CreateDCFromHandle(
                win32gui.CreateDC("WINSPOOL", printer, mode))
        except Exception:
            # A driver that will not take our DEVMODE still prints; it just
            # prints with its own defaults, which is better than not printing.
            pass
    context = win32ui.CreateDC()
    context.CreatePrinterDC(printer)
    return context


def print_document(doc: fitz.Document, printer: str | None = None,
                   pages: str = "", copies: int = 1,
                   title: str = "Platen PDF", dpi: int = DEFAULT_DPI,
                   destination: str | None = None, subset: str = "all",
                   reverse: bool = False, collate: bool = True,
                   orientation: str = "auto", per_sheet: int = 1,
                   duplex: str = "none", colour: bool = True,
                   scale: str = "fit") -> dict:
    """Send the document to a printer. Returns what was actually sent."""
    try:
        import win32print  # noqa: F401  - proves the support is installed
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
    wanted = apply_subset(wanted, subset)
    if not wanted:
        word = "odd" if subset == "odd" else "even"
        raise PdfError("That selection has no %s-numbered pages in it." % word)

    copies = max(1, min(int(copies or 1), 99))
    dpi = max(72, min(int(dpi or DEFAULT_DPI), MAX_DPI))
    per_sheet = int(per_sheet or 1)
    if per_sheet not in SHEET_GRIDS:
        per_sheet = 1
    sheets = build_sheets(wanted, per_sheet, reverse, copies, collate)

    try:
        context = _context(chosen, orientation, duplex, bool(colour),
                           bool(collate))
    except Exception as exc:
        raise PdfError("Could not reach the printer %r. Is it still "
                       "installed?" % chosen) from exc

    sent = 0
    try:
        area_w = context.GetDeviceCaps(HORZRES)
        area_h = context.GetDeviceCaps(VERTRES)
        across, down = grid_for(per_sheet, area_w > area_h)
        # A gap only matters once there is more than one page on the sheet.
        gap = 0 if per_sheet == 1 else max(8, min(area_w, area_h) // 80)
        # A file-port printer refuses to start without somewhere to write,
        # with nothing but "StartDoc failed" to explain itself.
        if destination:
            context.StartDoc(title, destination)
        else:
            context.StartDoc(title)
        try:
            for sheet in sheets:
                context.StartPage()
                for slot, index in enumerate(sheet):
                    page = doc[index]
                    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    image = Image.frombytes(
                        "RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    if not colour:
                        # Done here as well as in the DEVMODE: a driver that
                        # ignores the request would otherwise print colour
                        # from a job that asked for black and white.
                        image = image.convert("L").convert("RGB")
                    left, top, width, height = cell_box(
                        slot, across, down, area_w, area_h, gap)
                    if scale == "actual" and per_sheet == 1:
                        # One PDF point is 1/72 inch, so at the printer's own
                        # resolution the page occupies its true size.
                        wide = int(round(page.rect.width / 72.0 *
                                         context.GetDeviceCaps(LOGPIXELSX)))
                        tall = int(round(page.rect.height / 72.0 *
                                         context.GetDeviceCaps(LOGPIXELSY)))
                        box = (max(0, (area_w - wide) // 2),
                               max(0, (area_h - tall) // 2))
                        box = (box[0], box[1], box[0] + wide, box[1] + tall)
                    else:
                        inner = fit_box(pixmap.width, pixmap.height, width, height)
                        box = (left + inner[0], top + inner[1],
                               left + inner[2], top + inner[3])
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
            "sheets": sent, "file": destination, "per_sheet": per_sheet,
            "subset": subset, "duplex": duplex, "collated": bool(collate)}
