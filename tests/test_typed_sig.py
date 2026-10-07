"""Typing a signature instead of scanning one, and the window coming forward.

Three things people asked for, which share nothing but this file:
typed signatures, opening a PDF raising the window, and opening one that is
already open switching to it rather than loading a second copy.
"""
import io
import os
import shutil
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402

import webview  # noqa: E402
from PIL import Image  # noqa: E402

from backend import foreground, signatures  # noqa: E402
from backend.api import Api  # noqa: E402
from backend.session import PdfError  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")
PDF = os.path.join(ROOT, "tests", "output", "fixture.pdf")
COPY = os.path.join(ROOT, "tests", "output", "fixture-second.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def wipe():
    for item in signatures._load():
        try:
            os.remove(os.path.join(signatures.SIG_DIR, item["file"]))
        except OSError:
            pass
    signatures._store([])


print("== the handwriting faces ==")
faces = signatures.fonts()
check("some are found", len(faces), lambda n: n >= 1)
check("each has an id and a label",
      all(f.get("id") and f.get("label") for f in faces), True)
check("and each file really exists",
      all(os.path.isfile(os.path.join(signatures.FONT_DIR, f["id"])) for f in faces), True)

print("")
print("== rendering typed text ==")
data = signatures.render_typed("David Willmore", faces[0]["id"], height=150)
image = Image.open(io.BytesIO(data))
check("it is a PNG", data[:8], b"\x89PNG\r\n\x1a\n")
check("with transparency, so it sits on the page", image.mode, "RGBA")
check("and a sensible size", image.size, lambda s: s[0] > 120 and 40 < s[1] < 400)
check("it is wider than it is tall, like a signature",
      image.width > image.height, True)

# Cropped close to the ink, but not flush to it: a small margin is added on
# purpose, because a stroke touching the edge looks clipped once scaled onto a
# page. So what matters is that the empty border is thin, not that it is zero -
# dead space is what would shrink the signature inside its box.
alpha = image.split()[3]
columns = [max(alpha.crop((x, 0, x + 1, image.height)).getdata())
           for x in range(image.width)]
first_ink = next(x for x, v in enumerate(columns) if v > 0)
last_ink = image.width - 1 - next(x for x, v in enumerate(reversed(columns)) if v > 0)
check("the margin on the left is small",
      first_ink, lambda x: x <= max(6, image.width * 0.06))
check("and on the right",
      image.width - 1 - last_ink, lambda x: x <= max(6, image.width * 0.06))
check("but there is some, so strokes do not look clipped", first_ink, lambda x: x >= 1)

print("")
print("== different hands really look different ==")
sizes = set()
for face in faces[:5]:
    rendered = Image.open(io.BytesIO(signatures.render_typed("Sample Name", face["id"], 120)))
    sizes.add(rendered.size)
check("five faces give five different shapes", len(sizes), lambda n: n >= 4)

print("")
print("== what it refuses ==")
for text, label in [("", "nothing typed"), ("   ", "only spaces")]:
    try:
        signatures.render_typed(text, faces[0]["id"])
        check("refuses %s" % label, False, "it was accepted")
    except PdfError as exc:
        check("refuses %s" % label, "Type a name" in str(exc), True)
try:
    out = signatures.render_typed("Fallback", "no-such-font.ttf")
    check("an unknown font falls back rather than failing", len(out) > 100, True)
except PdfError as exc:
    check("an unknown font falls back rather than failing", str(exc), "it raised")

print("")
print("== saving one ==")
wipe()
entry = signatures.add_typed("Alex Morgan", faces[0]["id"], role="Director")
check("it lands in the library", len(signatures._load()), 1)
check("named after the text", entry["name"], "Alex Morgan")
check("with the role", entry["role"], "Director")
check("and the file is there",
      os.path.isfile(os.path.join(signatures.SIG_DIR, entry["file"])), True)
saved = Image.open(os.path.join(signatures.SIG_DIR, entry["file"]))
check("the saved image kept its transparency", saved.mode, lambda m: "A" in m)
wipe()

print("")
print("== opening a document that is already open ==")
shutil.copyfile(PDF, COPY)
api = Api()
first = api.open_path(PDF)
check("the first open works", first["ok"], True)
check("one tab", len(api.tab_list()["data"]["tabs"]), 1)

again = api.open_path(PDF)
check("opening the same file again is allowed", again["ok"], True)
check("it says it was already open", again["data"].get("already_open"), True)
check("and did not add a tab", len(api.tab_list()["data"]["tabs"]), 1)

other = api.open_path(COPY)
check("a different file does open a tab", other["ok"], True)
check("now two tabs", len(api.tab_list()["data"]["tabs"]), 2)
check("and that one is not flagged as already open",
      other["data"].get("already_open"), None)

back = api.open_path(PDF)
check("going back to the first switches rather than reopening",
      back["data"].get("already_open"), True)
check("still two tabs", len(api.tab_list()["data"]["tabs"]), 2)
check("and the first is the active one",
      api.tab_list()["data"].get("active"), 0)

# The same file by a messier path is still the same file.
messy = os.path.join(os.path.dirname(PDF), ".", os.path.basename(PDF).upper())
if os.path.isfile(messy):
    check("a differently spelled path is recognised",
          api.open_path(messy)["data"].get("already_open"), True)
try:
    os.remove(COPY)
except OSError:
    pass

print("")
print("== the window can raise itself ==")
check("the helper exists", callable(foreground.bring_to_front), True)
check("granting foreground does not throw",
      isinstance(foreground.allow_any_process(), bool), True)
check("asking to raise with no window is a quiet no, not a crash",
      foreground.bring_to_front(0), False)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    print("")
    print("== and from the window ==")
    check("there is a Type a signature button",
          js("!!document.getElementById('sig-type')"), True)
    check("the image route is still there",
          js("!!document.getElementById('sig-add')"), True)

    js("document.getElementById('sig-type').click()")
    time.sleep(2.0)
    check("the dialog opens",
          js("document.getElementById('modal-back').classList.contains('on')"), True)
    check("it offers handwriting styles",
          js("""(document.querySelector('#modal select[name="font"]') || {length: 0})
                 .length"""), lambda n: n >= 1)
    check("and a preview box",
          js("!!document.getElementById('typed-preview')"), True)
    check("which starts by asking for a name",
          js("(document.getElementById('typed-preview').innerText || '').trim()"),
          lambda t: "Type a name" in (t or ""))

    js("""(function () {
        const f = document.querySelector('#modal input[name="text"]');
        f.value = 'Jordan Reeves';
        f.oninput();
      })()""")
    time.sleep(3.0)
    check("typing produces a picture",
          js("!!document.querySelector('#typed-preview img')"), True)
    check("and it is a real image",
          js("""(function () {
                const i = document.querySelector('#typed-preview img');
                return i ? i.src.slice(0, 22) : '';
              })()"""), "data:image/png;base64,")
    check("the dialog is honest about what this is",
          js("(document.getElementById('modal').innerText || '').toLowerCase()"),
          lambda t: "not a certificate" in t)

    js("document.querySelector('#modal [data-x]').click()")
    window.destroy()


api2 = Api()
w = webview.create_window("Platen PDF typed", url=UI, js_api=api2,
                          width=1250, height=900, hidden=True)
api2.attach_window(w)
webview.start(drive, w)
wipe()

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
