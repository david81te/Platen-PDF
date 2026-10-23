# Working on Platen PDF

Context for anyone — or any Claude session — picking this up on another
machine. The code is the source of truth; this is the part the code does not
say out loud.

## What it is

A standalone Windows PDF editor. Python 3.12 + PyMuPDF for the PDF work,
pywebview + WebView2 for the window, PyInstaller for the build. No installer
dependencies: everything, OCR models included, ships inside the exe.

Published at **github.com/david81te/Platen-PDF**, site at
**freepdf4you.com** (Vercel, deployed from `site/` with the CLI, not from a
git hook — pushing does *not* deploy it).

Copyright is **Webbr Labs, LLC**, not a person. Licence is **AGPL-3.0 and that
is not optional**: PyMuPDF is AGPL-or-commercial, so distributing a binary
obliges the matching public source. Do not advise closing the source.

## Layout

```
app.py              window, CLI flags, single-instance handoff
backend/            one module per concern; api.py is the only thing the UI talks to
ui/                 index.html + app.js + styles.css, no framework, no build step
tests/              ~36 suites, each a standalone script, run directly
build.ps1           -> dist/PlatenPDF/            (folder build, what ships)
package.ps1         -> dist/PlatenPDF-Setup.zip   (what people download)
```

`backend/api.py` is the boundary. Everything the window can do is an
`@endpoint` there; the UI never imports anything else.

## Things that will bite you

**`win32print.DeviceCapabilities` does not release the GIL.** Asking an
unreachable network printer what it supports blocks for ~50 seconds *and
freezes every Python thread in the process*, including the one keeping the
window alive — so Windows closes the app as unresponsive. No thread or timeout
helps; even `Thread.start()` blocks behind it. This shipped once (v1.3.0) and
had to be hotfixed by removing the call. Assume any pywin32 call that talks to
hardware can do this.

Still true and not yet fixed: printing *to* an unreachable printer blocks ~50s
in `CreatePrinterDC`. Pre-existing since v1.1.0. The real fix is running the
print job in its own process.

**A PDF radio group is several widgets sharing one field name.** Looking a
field up by name finds the first button whatever was clicked, which silently
records the wrong answer. Always address widgets by `xref`
(`forms.set_widget`), and select a radio by writing its own on-state name.

**Setting a checkbox rewrites its appearance stream**, so the page already
draws the tick. Do not draw a second one in the overlay.

**Elements inside the viewer are found by class, not id**, because the window
can be split and there may be two of each. Pane 0 keeps the original ids for
the benefit of existing tests. If you clear a class list on one of those
elements you destroy the only handle on it — `buildOverlay` learned this the
hard way.

**The inspector's side panels have been `class="pane"` for ever.** The
viewer's panes are `vpane`.

**Closing a tab renumbers every tab after it.** Anything holding tab indices
(the panes do) must shift them, or it ends up pointing at a different
document.

## Tests

Run them all before a release: `.venv\Scripts\python.exe tests\test_x.py`, one
at a time. Several "flakes" in this project turned out to be real bugs — a
missing lock, a same-second timestamp comparison, a stale-state race. Treat an
intermittent failure as a bug until proven otherwise.

Three suites (`test_account`, `test_account_ui`, `test_sync`) exit **2**, not
1, when `SUPABASE_SERVICE_KEY` is absent. That is "could not run", not
"passed" — and they have not been run since the account/sync code shipped.

Never start editing while a full suite run is in flight. The run tests a
moving target and its result is worthless.

Each suite builds its fixtures in its own temp directory (`tests/sandbox.py`).
Do not share fixture files between suites: a hung process holding one makes a
different suite fail for a reason unrelated to what it tests.

The screen-capture test (`test_colour`) proves our own window is in front at
six points before photographing anything. **Never weaken that guard, and never
make it steal focus to satisfy it** — it exists because a screenshot once
caught a browser window containing payment details.

## Releasing

1. Full suite green
2. Bump `backend/version.py` **and** `assets/version_info.txt` together
   (`test_about` fails if they drift)
3. `.\build.ps1` then `.\package.ps1` — package refuses to ship a build that
   fails its self-test
4. `gh release create vX.Y.Z dist\PlatenPDF-Setup.zip --repo david81te/Platen-PDF`
5. Update `site/index.html` version **and** deploy it — the download button
   serves GitHub's "latest", so a site claiming a version that is not released
   hands out something else
6. `test_updates` checks the tree is never behind the published release

PowerShell 5.1: commit messages with quotes need `git commit -F <file>`, and
`2>&1` on a native command turns its stderr into terminating errors — it is
what made `build.ps1` appear to fail.

## Standing decisions — do not re-litigate

- **No user should have to download anything** for a feature to work. OCR
  bundles RapidOCR ONNX models rather than requiring Tesseract.
- **The icon** is an Acrobat lookalike. The owner has reaffirmed it repeatedly.
- **No certificate-based signing.** PyMuPDF cannot create PKI signatures;
  pyHanko would be a real project. Typed and drawn signatures are pictures,
  and the UI says so.
- **A Claude sidebar is shelved.**
