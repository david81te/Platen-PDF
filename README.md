# PDF Studio

A standalone Windows PDF editor — Acrobat-style editing, conversion and signing,
in a single `.exe` with no install and no subscription.

Built for documents that started life as Word files, which is where in-place
text editing is most reliable.

## Build

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.\build.ps1
```

Produces `dist\PDFStudio.exe` (~108 MB, self-contained). Run it directly, or
pass a file: `PDFStudio.exe contract.pdf`. To run from source: `python app.py`.

## Features

**Edit text in place** — click any run of text to rewrite it; click the `¶`
marker beside a paragraph to rewrite the whole paragraph with re-wrapping.
Fonts, size, colour, bold/italic, alignment and line spacing are preserved.
Also: add text boxes, delete regions, find and replace across the document.

**Pages** — rotate, insert, delete, duplicate, move, crop, extract, merge
multiple files, split by count or ranges.

**Markup** — highlight, underline, strike-through (all snap to word
boundaries), sticky notes, free-text boxes, rectangles, ellipses, lines,
arrows, freehand ink, and the standard stamps (Approved, Draft, Confidential…).
Annotations can be flattened so they become permanent.

**Signatures** — save a signature image per person (the white paper background
is removed automatically and the image is cropped to the ink), then drag a box
to place one. Signatures are drawn into the page content stream, not added as
annotations, so once saved they cannot be selected, moved or deleted.
Deliberately not cryptographic signing — this is the "paste my signature"
workflow.

**Forms** — fill existing fields, create text/checkbox/dropdown fields, export
and re-import values as CSV, flatten filled fields into the page.

**Security** — AES-256 passwords for opening and for permissions, with
per-permission control. True redaction that deletes the underlying text and
image data rather than drawing a black box over it.

**Convert** — to Word (layout preserving), Excel (detected tables), PowerPoint
(editable text boxes or exact page pictures), images, plain text. Word, Excel,
PowerPoint and image files can be opened directly and become PDFs. OCR for
scanned pages. Compression with three levels.

**Also** — watermarks (text or image), page numbers, headers and footers, page
backgrounds, hyperlinks, bookmarks, document properties, 25-step undo.

## Requirements

- Windows with the Edge WebView2 runtime (present on Windows 10/11 by default).
- **Microsoft Office** — only for opening Word/Excel/PowerPoint files. Without
  it, LibreOffice is used if installed; otherwise those formats are unavailable.
  Everything else works with no external dependency.
- **Tesseract OCR** — only for the OCR command. Install from
  [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki); the
  app detects it on start and explains if it is missing.

## How in-place text editing works

A PDF stores positioned glyph draws, not editable paragraphs, so "editing text"
means removing the original glyphs and re-typesetting replacements:

1. The original text is removed with a redaction that preserves images and line
   art, so table borders and shading around the edit survive.
2. Replacement text is re-typeset with the original font — preferring the font
   installed on this machine over the PDF's embedded copy, because Word embeds
   only a *subset* of the glyphs the document happened to use, and a newly typed
   character would otherwise come out blank.
3. Editing a run redraws its whole line so neighbouring runs reflow instead of
   being clipped. Editing a paragraph re-wraps it, growing into free space below
   before shrinking the font, and never past whatever content sits underneath.
4. The font's ToUnicode table is rebuilt afterwards. MuPDF reverse-maps glyphs
   to codepoints and picks poorly when several codepoints share a glyph — spaces
   come back as U+00A0 and the "fi" ligature as a Greek beta. Text still *looks*
   right, but search, copy/paste and Word export would all see wrong characters.

**Limits.** Rotated and vertical text is refused rather than mangled. Scanned
pages have no text to edit until OCR is run. Paragraph edits apply the
paragraph's dominant style, so a run with unusual styling mid-paragraph is
normalised. Text longer than its original space is flagged rather than silently
shrunk to an illegible size.

## Tests

```powershell
.venv\Scripts\python tests\make_fixture.py   # builds a real Word-exported PDF
.venv\Scripts\python tests\test_textedit.py  # in-place editing scenarios
.venv\Scripts\python tests\test_backend.py   # every backend feature
.venv\Scripts\python tests\test_api.py       # the API surface the UI calls
```

`make_fixture.py` needs Word; the rest run against the PDF it produces.

## Layout

```
app.py              window + entry point
backend/
  api.py            the bridge the UI calls (all state is private -- see note below)
  session.py        open document, undo/redo, render, search, bookmarks
  textedit.py       in-place text editing
  fonts.py          font matching and ToUnicode repair
  pages.py          page organisation
  annots.py         markup, shapes, ink, images, links
  signatures.py     signature library and flattened placement
  forms.py          form fields
  security.py       passwords and redaction
  convert.py        conversion in and out, OCR, compression
  decorate.py       watermarks, page numbers, headers, backgrounds
ui/                 index.html, styles.css, app.js
```

Note: every attribute on the `Api` object is private. pywebview walks the public
attributes of the JS-API object, and anything public pointing at the native
window recurses through the WebView2 COM tree and silently kills the entire
bridge.
