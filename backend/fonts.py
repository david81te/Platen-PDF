"""Font resolution for in-place text editing.

Re-typesetting edited text only looks right if the replacement glyphs match the
font the original PDF used. We try, in order: a font installed on this machine
with a matching name, the PDF's own embedded font program, then a base-14 alias
chosen by name heuristics.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import pymupdf as fitz

SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")

# Word's default themes lean on these; map to the closest base-14 metric match.
BASE14_HINTS = (
    ("times", "tiro"),
    ("georgia", "tiro"),
    ("cambria", "tiro"),
    ("garamond", "tiro"),
    ("book", "tiro"),
    ("serif", "tiro"),
    ("courier", "cour"),
    ("consol", "cour"),
    ("mono", "cour"),
    ("symbol", "symb"),
    ("wingding", "zadb"),
    ("dingbat", "zadb"),
)

WINDOWS_FONT_DIRS = (
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
)


def normalize(name: str) -> str:
    """Strip the subset prefix and style suffix noise from a PDF font name."""
    if not name:
        return ""
    name = SUBSET_PREFIX.sub("", name)
    name = name.split("+")[-1]
    return name.strip()


def family_of(name: str) -> str:
    base = normalize(name)
    base = re.split(r"[-,]", base)[0]
    return re.sub(r"(MT|PS|PSMT|Std|Pro)$", "", base).lower()


def style_of(name: str) -> tuple[bool, bool]:
    low = normalize(name).lower()
    bold = "bold" in low or "black" in low or "heavy" in low or "semib" in low
    italic = "italic" in low or "oblique" in low
    return bold, italic


# PyMuPDF's names for the styled base-14 faces. Building them from the stem
# ("helvb") named fonts that do not exist, and bold fell back to regular.
_BASE14_STYLED = {
    ("helv", True, False): "hebo", ("helv", False, True): "heit", ("helv", True, True): "hebi",
    ("tiro", True, False): "tibo", ("tiro", False, True): "tiit", ("tiro", True, True): "tibi",
    ("cour", True, False): "cobo", ("cour", False, True): "coit", ("cour", True, True): "cobi",
}


def base14_for(name: str, bold: bool = False, italic: bool = False) -> str:
    low = normalize(name).lower()
    stem = "helv"
    for needle, target in BASE14_HINTS:
        if needle in low:
            stem = target
            break
    return _BASE14_STYLED.get((stem, bool(bold), bool(italic)), stem)


# Installed fonts that reach past Latin, in order: Arial and Segoe UI cover
# Greek, Cyrillic, Hebrew, Arabic and most punctuation; the rest East Asian.
UNICODE_FONTS = ("arial.ttf", "segoeui.ttf", "msyh.ttc", "malgun.ttf", "msgothic.ttc")


@lru_cache(maxsize=1)
def unicode_fonts() -> tuple[str, ...]:
    folder = WINDOWS_FONT_DIRS[0]
    return tuple(os.path.join(folder, name) for name in UNICODE_FONTS
                 if os.path.isfile(os.path.join(folder, name)))


def unicode_font_for(text: str) -> str | None:
    """An installed font that can draw every character of `text`, if any."""
    wanted = {ord(ch) for ch in text if not ch.isspace()}
    for path in unicode_fonts():
        try:
            font = fitz.Font(fontfile=path)
        except Exception:
            continue
        if all(font.has_glyph(cp) for cp in wanted):
            return path
    return None


@lru_cache(maxsize=1)
def _installed_fonts() -> dict[str, str]:
    """Map lowercase font-file stem -> full path for locally installed fonts."""
    found: dict[str, str] = {}
    for directory in WINDOWS_FONT_DIRS:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            stem = os.path.splitext(entry)[0].lower().replace(" ", "")
            found.setdefault(stem, os.path.join(directory, entry))
    return found


def _installed_candidates(family: str, bold: bool, italic: bool) -> list[str]:
    base = family.replace(" ", "")
    names = []
    if bold and italic:
        names += [base + "bi", base + "z", base + "bolditalic"]
    if bold:
        names += [base + "bd", base + "b", base + "bold"]
    if italic:
        names += [base + "i", base + "italic"]
    names += [base, base + "r", base + "regular"]
    return names


def local_font_path(pdf_font_name: str) -> str | None:
    """Find an installed .ttf/.otf whose name matches this PDF font."""
    family = family_of(pdf_font_name)
    if not family:
        return None
    bold, italic = style_of(pdf_font_name)
    table = _installed_fonts()
    for candidate in _installed_candidates(family, bold, italic):
        hit = table.get(candidate)
        if hit:
            return hit
    return None


class FontResolver:
    """Resolves the fonts used on a page, caching extracted font programs.

    One instance per edit operation; it holds onto font buffers so a paragraph
    rewrite doesn't re-extract the same font for every span.
    """

    def __init__(self, doc: fitz.Document):
        self.doc = doc
        self._page_cache: dict[int, dict[str, int]] = {}
        self._buffer_cache: dict[int, bytes | None] = {}

    def _page_font_xrefs(self, page: fitz.Page) -> dict[str, int]:
        """Map normalized font name -> xref for fonts referenced by this page."""
        cached = self._page_cache.get(page.number)
        if cached is not None:
            return cached
        table: dict[str, int] = {}
        for item in page.get_fonts(full=False):
            xref, basefont = item[0], item[3]
            key = normalize(basefont).lower()
            if key:
                table.setdefault(key, xref)
        self._page_cache[page.number] = table
        return table

    def embedded_buffer(self, page: fitz.Page, font_name: str) -> bytes | None:
        """Return the embedded font program for this span's font, if extractable."""
        xref = self._page_font_xrefs(page).get(normalize(font_name).lower())
        if not xref:
            return None
        if xref in self._buffer_cache:
            return self._buffer_cache[xref]
        buffer = None
        try:
            _, ext, _, raw = self.doc.extract_font(xref)
            # Type1/CFF subsets often can't be re-embedded cleanly; only reuse
            # formats PyMuPDF can hand straight back to the typesetter.
            if raw and ext in ("ttf", "otf", "cff", "woff", "woff2"):
                buffer = bytes(raw)
        except Exception:
            buffer = None
        self._buffer_cache[xref] = buffer
        return buffer

    def resolve(self, page: fitz.Page, font_name: str) -> dict:
        """Pick the best available font source for re-typesetting `font_name`.

        Installed fonts win over the PDF's own embedded copy: Word subsets its
        embedded fonts to only the glyphs the original document used, so typing
        a new character against one yields a blank box. A locally installed
        Calibri/Times has the full range.
        """
        bold, italic = style_of(font_name)
        path = local_font_path(font_name)
        if path:
            return {
                "kind": "file",
                "path": path,
                "bold": bold,
                "italic": italic,
                "family": family_of(font_name) or "installed",
            }
        buffer = self.embedded_buffer(page, font_name)
        if buffer:
            return {
                "kind": "embedded",
                "buffer": buffer,
                "bold": bold,
                "italic": italic,
                "family": family_of(font_name) or "embedded",
            }
        return {
            "kind": "base14",
            "base14": base14_for(font_name, bold, italic),
            "bold": bold,
            "italic": italic,
            "family": family_of(font_name) or "helvetica",
        }


def css_family(font_name: str) -> str:
    """A CSS-safe family name derived from a PDF font name."""
    fam = family_of(font_name) or "body"
    return re.sub(r"[^a-z0-9]", "", fam) or "body"


# ---------------------------------------------------------------------------
# ToUnicode repair
#
# MuPDF builds a font's ToUnicode CMap by reverse-mapping each glyph to a
# codepoint, and picks a poor winner when several codepoints share one glyph:
# the space glyph resolves to U+00A0, and the "fi" ligature to a Greek beta.
# Text still *looks* right but extraction, search, copy/paste and Word export
# all see the wrong characters. For fonts we embed ourselves we simply rebuild
# the CMap from the font program, so every glyph maps back to the plain
# character a reader expects.
# ---------------------------------------------------------------------------

LIGATURES = {0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl"}

# Every character the font has, in ascending order: the first codepoint
# claiming a glyph wins, so U+0020 beats U+00A0 for the space glyph. A fixed
# list of Latin ranges left Cyrillic, Greek and the rest unmapped, and they
# came out of search and copy as glyph numbers.

CMAP_HEADER = """/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo <</Registry(Adobe)/Ordering(UCS)/Supplement 0>> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
"""

CMAP_FOOTER = """endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def _glyph_to_text(font) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        codepoints = sorted(font.valid_codepoints())
    except Exception:
        codepoints = list(range(0x20, 0x250))
    for cp in codepoints:
        if cp < 0x20 or 0xD800 <= cp <= 0xDFFF or cp in LIGATURES:
            continue
        try:
            gid = font.has_glyph(cp)
        except Exception:
            continue
        if gid:
            mapping.setdefault(gid, chr(cp))
    for cp, expansion in LIGATURES.items():
        try:
            gid = font.has_glyph(cp)
        except Exception:
            continue
        if gid:
            mapping[gid] = expansion  # prefer "fi" over the ligature codepoint
    return mapping


def build_tounicode(font) -> bytes | None:
    mapping = _glyph_to_text(font)
    if not mapping:
        return None
    entries = [
        "<%04x> <%s>" % (gid, "".join("%04x" % ord(ch) for ch in text))
        for gid, text in sorted(mapping.items())
    ]
    body = []
    for start in range(0, len(entries), 100):
        chunk = entries[start:start + 100]
        body.append("%d beginbfchar\n%s\nendbfchar\n" % (len(chunk), "\n".join(chunk)))
    return (CMAP_HEADER + "".join(body) + CMAP_FOOTER).encode("latin-1")


def page_font_xrefs(doc, page_number: int) -> set[int]:
    try:
        return {item[0] for item in doc.get_page_fonts(page_number, full=False)}
    except Exception:
        return set()


def _rebuild_font_tounicode(doc, font_xref: int) -> bool:
    """Regenerate one embedded font's ToUnicode CMap from its font program."""
    try:
        kind, value = doc.xref_get_key(font_xref, "ToUnicode")
        if kind != "xref":
            return False
        stream_xref = int(value.split()[0])
        _, ext, _, buffer = doc.extract_font(font_xref)
    except Exception:
        return False
    if not buffer or ext not in ("ttf", "otf", "cff", "woff", "woff2"):
        return False
    try:
        cmap = build_tounicode(fitz.Font(fontbuffer=bytes(buffer)))
    except Exception:
        return False
    if not cmap:
        return False
    try:
        doc.update_stream(stream_xref, cmap, compress=True)
        return True
    except Exception:
        return False


def repair_inserted_fonts(doc, page_number: int, known_before: set[int]) -> int:
    """Rebuild ToUnicode for fonts embedded since `known_before` was captured."""
    repaired = 0
    for xref in page_font_xrefs(doc, page_number) - known_before:
        if _rebuild_font_tounicode(doc, xref):
            repaired += 1
    return repaired
