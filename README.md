# PDF Studio

A standalone Windows PDF editor — Acrobat-style editing, conversion and signing,
in a single `.exe` with no install and no subscription.

Built for documents that started life as Word files, which is where in-place
text editing is most reliable.

## Build

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.\build.ps1              # folder build  -> dist\PDFStudio\PDFStudio.exe
.\build.ps1 -Portable    # single file   -> dist\PDFStudio.exe
```

Two flavours, same app:

| | Folder build | Single file |
| --- | --- | --- |
| Starts in | ~1s | ~12s |
| Opens a file in a running window | ~0.6s | ~4s |
| Size | 336 MB folder | 139 MB, one file |
| Best for | Everyday use, and as the default PDF app | Copying to another PC |

The single file unpacks itself to a temp folder on every launch, which is what
makes it slow to start. Use the folder build unless you need portability.

Run either directly, or pass a file: `PDFStudio.exe contract.pdf`. To run from
source: `python app.py`.

## Icon

`assets/app_icon_source.png` is the artwork. Rebuild the multi-size icon after
changing it:

```powershell
.venv\Scripts\python assets\make_app_icon.py
```

That writes `assets/pdfstudio.ico` with 16-256px entries, which both spec files
embed in the executable and which the file association points at. Detailed
artwork softens below about 32px, which is the size Explorer's list view and
the taskbar use, so check `assets/icon_sizes.png` after any change.

## Sharing it with other people

Both builds are self-contained: no Python, no installer, nothing to set up.
Send `dist\PDFStudio.exe` (single file) or a zip of `dist\PDFStudio\`.
Verified by running a copy in an empty folder with no Python or project files
present.

What a recipient needs:

- **64-bit Windows.** Not ARM Windows, not macOS.
- **Edge WebView2**, which Windows 11 and up-to-date Windows 10 already have.
  If it is missing the app says so and links to the free Microsoft installer
  rather than failing silently.
- **Microsoft Office or LibreOffice — only** to open Word/Excel/PowerPoint
  files. Everything else, including OCR, works without either, and the app
  explains the limit instead of erroring out.

What to expect:

- **SmartScreen will warn** ("Windows protected your PC") because the file is
  not code-signed: More info > Run anyway. Silencing this needs a paid signing
  certificate.
- **Some antivirus flags PyInstaller single-file builds.** The folder build is
  flagged less often, being an ordinary exe beside its libraries.
- **Too big to email** at 139 MB. Use OneDrive, SharePoint or Teams.

Each person gets their own signatures (`%APPDATA%\PDFEditorPro`) and their own
file associations. Nothing is written outside the user profile, and nothing
outside `HKEY_CURRENT_USER` in the registry.

## Opening PDFs from Explorer

```powershell
.\dist\PDFStudio\PDFStudio.exe --register      # add to the Windows PDF apps
.\dist\PDFStudio\PDFStudio.exe --unregister    # remove again
```

Or use **File > Set as default PDF app...** in the app.

This registers under `HKEY_CURRENT_USER`, so it needs no administrator rights
and affects only your account. It does **not** change your current default:
since Windows 8 an application cannot make itself the default handler -- the
choice lives in a `UserChoice` key that Windows protects with a hash it
verifies. Registering adds PDF Studio to *Open with* and to *Settings > Apps >
Default apps*, and the registration opens that screen so you can confirm.

Opening several PDFs from Explorer does not start several copies: the first
instance listens on a per-user named pipe, and later launches hand their file
over and exit, so each document arrives as a new tab.

## Features

**Edit text in place** — click any run of text to rewrite it; click the `¶`
marker beside a paragraph to rewrite the whole paragraph with re-wrapping.
Fonts, size, colour, bold/italic, alignment and line spacing are preserved.
Also: add text boxes, delete regions, find and replace across the document.

**Multiple documents** — open as many as you like in tabs across the top. Each keeps its own page, zoom, search results and undo history. A dot marks unsaved changes, and closing a dirty tab asks first.

**Pages** — rotate, insert, delete, duplicate, move, crop, merge other files
in, merge another open tab in, and split by page count or ranges. Extracting
pages opens them in a new tab, optionally saves them to a file, and can delete
them from the original in the same step (undoable, and refused if it would
empty the document).

**Markup** — highlight, underline, strike-through (all snap to word
boundaries), sticky notes, free-text boxes, rectangles, ellipses, lines,
arrows, freehand ink, and the standard stamps (Approved, Draft, Confidential…).
Click anything you have marked up to read it in a bubble on the page, with
the full text also listed in the Notes panel; both offer Edit and Delete.
Annotations can be flattened so they become permanent.
boundaries), sticky notes, free-text boxes, rectangles, ellipses, lines,
arrows, freehand ink, and the standard stamps (Approved, Draft, Confidential…).
Annotations can be flattened so they become permanent.

**Signatures** — save a signature image per person (the white paper background
is removed automatically and the image is cropped to the ink), then drag a box
to place one. A placed signature stays selectable: drag to move it, use the
handles to resize it, or delete it. It becomes part of the page — and stops
being selectable — when you flatten it, via **Protect ▸ Flatten annotations**.
Deliberately not cryptographic signing; this is the "paste my signature"
workflow.

**Objects you can come back to** — text boxes, signatures, shapes and arrows
are annotations until you flatten them, so they can be moved, resized,
reshaped, retyped and removed. Clicking one shows a frame with eight handles;
double-clicking a text box edits its words. Lines and arrows get endpoint grips
instead of a box, so the point can be aimed anywhere, plus a grip in the middle
to slide the whole thing. Flattening draws them into the page permanently and
leaves it looking pixel-for-pixel the same.

**Forms** — fill existing fields, create text/checkbox/dropdown fields, export
and re-import values as CSV, flatten filled fields into the page.

**Security** — AES-256 passwords for opening and for permissions, with
per-permission control. Password-protected files prompt for the password on
open and keep their protection when saved. True redaction that deletes the
underlying text and image data rather than drawing a black box over it.

**Compare two documents** — aligns pages by similarity first, so inserting a
page does not report every page after it as rewritten. Then a word-level diff
with before-and-after text, and a coarse pixel pass that catches changes the
words cannot see, like a moved logo or redrawn chart. Differences are listed
and drawn on the page, and can be marked into the document as highlights and
notes.

**Search** — full-document find with a results list, surrounding-line context,
match highlighting, match-case, next/previous navigation, and every hit drawn
on the page. `Ctrl+F`.

**Convert** — to Word (layout preserving), Excel (detected tables), PowerPoint
(editable text boxes or exact page pictures), images, plain text. Word, Excel,
PowerPoint and image files can be opened directly and become PDFs. Compression
with three levels.

**OCR** — the engine (RapidOCR on onnxruntime) ships **inside the app**; there
is nothing for anyone to install. A recognised page keeps its original pixels
exactly and gains an invisible text layer, so scans become searchable and
selectable without changing how they look.

**Also** — watermarks (text or image), page numbers, headers and footers, page
backgrounds, hyperlinks, bookmarks, document properties, 25-step undo.

## Mouse and keyboard

| Action | Result |
| --- | --- |
| Zoom box | Fit width, fit page, or a set percentage |
| Wheel | Scroll the page; at the top or bottom edge, turn to the previous/next page |
| Ctrl + wheel | Zoom in and out |
| Ctrl+O / Ctrl+S | Open / Save |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+F | Find |
| Page Up/Down, arrows | Previous / next page |
| Esc | Cancel the current edit and return to the Select tool |

The page refits whenever the window changes size, including maximising, unless a
fixed percentage has been chosen. Fit width is the default.

When a page already fits the window there is nothing to scroll, so the wheel
turns pages immediately.

## Requirements

- Windows with the Edge WebView2 runtime (present on Windows 10/11 by default).
- **Microsoft Office** — only for opening Word/Excel/PowerPoint files. Without
  it, LibreOffice is used if installed; otherwise those formats are unavailable.
  Everything else works with no external dependency.

Nothing else is required. In particular **OCR needs no install** — the engine
and its models live inside the executable.

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

When replacement text no longer fits its line, the whole paragraph is
re-wrapped rather than the line alone: wrapping just that line would lay it
over the lines beneath, and drawing it anyway would push the tail off the page
where it is invisible.

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
.venv\Scripts\python tests\test_ocr.py       # scan -> searchable, pixels unchanged
.venv\Scripts\python tests\test_ui.py        # drives the real front end
.venv\Scripts\python tests\test_buttons.py  # clicks every control in the window
.venv\Scripts\python tests\test_delete.py   # Delete removes every kind of object
.venv\Scripts\python tests\test_comments.py # clicking a comment opens it
.venv\Scripts\python tests\make_hard.py     # awkward multi-page fixture
.venv\Scripts\python tests\test_edge.py     # edge cases and error paths
.venv\Scripts\python tests\test_flows.py    # multi-step workflows
.venv\Scripts\python tests\test_shell.py    # file association + single instance
```

`make_fixture.py` needs Word; the rest run against the PDF it produces.
`test_ui.py` opens the real window and drives `app.js` through `evaluate_js`.
It never synthesises mouse or keyboard input, so it cannot disturb whatever
else is open on the desktop — do not replace it with input automation.

To check a packaged build without a GUI:

```powershell
.\dist\PDFStudio.exe --selftest
```

This confirms the bundled UI files and OCR models resolved inside the frozen
executable — the part most likely to break when PyInstaller relocates data.

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

## Notes for future work

- **Search builds a line index per page, once.** Asking MuPDF for the text
  around each hit instead took 10 seconds on a 160-page document, which is
  unusable for a box that searches as you type. The same applies to the
  match-case check, which needs the words rather than the hit rectangle.
- **Thumbnails are placeholders until they scroll into view.** Rendering
  every page up front was the other thing that made opening a long document
  feel slow.
- **`S` is a top-level `const`, not `window.S`.** A classic script's top-level
  `const` is a lexical binding, so a readiness probe testing `window.S` is
  always false and the wait loop silently falls through. Test `typeof S`.
- **Panels that read state fetched during `buildOverlay` must redraw after
  it resolves.** `setTool` draws the inspector first, so the form panel
  rendered empty on a document that plainly had fields.
- **A failed PyInstaller run leaves the previous `dist/` in place.** It is
  easy to keep testing a stale executable and conclude a fix did not work.
  `build.ps1` now checks the exit code and stops loudly; if a change seems
  absent from the app, check the timestamp on the exe first.
- **A running or registered exe locks `dist/`**, which is another way a
  rebuild fails while leaving a stale binary. Close the app before building.
- **A failed PyInstaller run leaves the previous `dist/` in place.** It is
  easy to keep testing a stale executable and conclude a fix did not work,
  so `build.ps1` reports a failed build loudly and stops. Check the
  timestamp on the exe if something you just changed seems absent.
Behaviour that looks like a bug but is deliberate, and traps worth knowing:

- **Never read `doc.needs_pass` after authenticating.** It re-locks the
  document: rendering keeps working while text extraction, search and export
  silently start returning nothing. `Session` records the state once, at open.
- **`replace_all` rewrites each run at most once.** Re-scanning after every
  edit never terminates when the replacement contains the search text
  (replacing `ABC` with `ABC-ABC`).
- **`pages.move(a, b)` inserts *before* what is at `b`**, so moving forwards
  lands the page one index earlier than `b`. Use `reorder()` for exact
  positions.
- **OCR raises when every selected page already has text**, rather than
  silently doing nothing, so the UI can point at the Force option.
- **`Api` attributes are all private.** A public one pointing at the native
  window recurses through the WebView2 COM tree and kills the whole JS bridge.
- **Do not test the UI with synthetic mouse or keyboard input.** It types into
  whatever happens to have focus. `tests/test_ui.py` drives `app.js` through
  `evaluate_js` instead.
