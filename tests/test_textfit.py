"""Every control drawn over the page must be tall enough for its own text.

The sticky-note bubble's Delete button was shorter than the word on it, so the
red background stopped above the bottom of the letters. The cause was
line-height: 0 on #pagewrap, inherited by everything inside it, which is the
sort of fault that hits a dozen places and gets reported as one.

So this does not test that button. It walks everything on the overlay that
carries text and checks the box is big enough to hold it.
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402

import webview  # noqa: E402

from backend.api import Api  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")
PDF = os.path.join(ROOT, "tests", "output", "fixture.pdf")
fails = []

# Walks every element that holds text, and reports any whose box is too short
# to contain a line of it. 0.92 rather than 1.0 because a cap-height-only
# string legitimately needs a shade less than the full line box.
SURVEY = """
(function (scope) {
  const bad = [];
  document.querySelectorAll(scope + ' *').forEach(function (el) {
    const text = (el.textContent || '').trim();
    if (!text || el.children.length) return;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return;
    const box = el.getBoundingClientRect();
    if (!box.height) return;
    const size = parseFloat(style.fontSize) || 0;
    const pad = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const line = style.lineHeight === 'normal'
      ? size * 1.2 : (parseFloat(style.lineHeight) || 0);
    if (line < size * 0.92 || box.height < size * 0.92 + pad) {
      bad.push(el.tagName.toLowerCase()
        + (el.className && typeof el.className === 'string'
           ? '.' + el.className.trim().split(/ +/).join('.') : '')
        + ' "' + text.slice(0, 14) + '" h=' + box.height.toFixed(1)
        + ' font=' + size + ' line=' + line.toFixed(1));
    }
  });
  return bad.join(' | ');
})(SCOPE)
"""


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %s" % ("ok  " if ok else "BUG ", label, got), flush=True)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.confirm = () => true;")
    js("window.openOnStart(%s)" % repr(PDF.replace("\\", "/")))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(2.5)

    print("== nothing over the page inherits a zero line box ==")
    check("the page wrapper has a real line-height",
          js("getComputedStyle(document.getElementById('pagewrap')).lineHeight"),
          lambda v: v != "0px")
    check("and so does the overlay",
          js("getComputedStyle(document.getElementById('overlay')).lineHeight"),
          lambda v: v != "0px")

    print("")
    print("== the sticky note that started this ==")
    js("window.pywebview.api.annot_note(0,[300,300],'A note to delete')")
    time.sleep(1.8)
    js("setTool('select')")
    time.sleep(1.4)
    js("if (document.querySelector('.annothit')) document.querySelector('.annothit').click()")
    time.sleep(1.8)
    check("the bubble opened", js("!!document.querySelector('.bubble')"), True)

    measured = js("""(function () {
        const b = document.querySelector('.bubble [data-del]');
        if (!b) return null;
        const s = getComputedStyle(b), r = b.getBoundingClientRect();
        return JSON.stringify({
          height: +r.height.toFixed(1),
          font: parseFloat(s.fontSize),
          line: s.lineHeight,
          padTop: parseFloat(s.paddingTop),
          padBottom: parseFloat(s.paddingBottom)
        });
      })()""")
    print("  Delete button:", measured)
    import json
    box = json.loads(measured) if measured else {}
    check("its line box is not zero", box.get("line"), lambda v: v != "0px")
    check("the red covers the whole word, descenders included",
          box.get("height", 0),
          lambda h: h >= box.get("font", 99) + box.get("padTop", 0) + box.get("padBottom", 0))
    check("padding is even top and bottom",
          (box.get("padTop"), box.get("padBottom")),
          lambda p: p[0] == p[1])

    print("")
    print("== the same check across the whole interface ==")
    # Open everything that is normally shut, so the survey sees it.
    states = [
        ("the page and its overlay", "#pagewrap"),
        ("the toolbar and menu bar", "#menubar, #toolbar"),
        ("the left panel", "#sidebar"),
        ("the right panel", "#inspector"),
        ("the status bar", "#statusbar"),
        ("the whole window", "body"),
    ]
    for label, scope in states:
        survey = js(SURVEY.replace("SCOPE", repr(scope)))
        check(label, survey, "")
        if survey:
            for item in survey.split(" | "):
                print("      " + item)

    print("")
    print("== with the menus and dialogs open ==")
    for menu in ("file", "edit", "pages", "insert", "convert", "protect", "help"):
        js("document.querySelectorAll('.menu').forEach(m => m.classList.remove('open'));"
           "document.querySelector('[data-menu=\"%s\"]').classList.add('open')" % menu)
        time.sleep(0.35)
        survey = js(SURVEY.replace("SCOPE", repr("[data-menu=\"%s\"]" % menu)))
        check("%s menu" % menu, survey, "")
        if survey:
            for item in survey.split(" | "):
                print("      " + item)
    js("document.querySelectorAll('.menu').forEach(m => m.classList.remove('open'))")

    for tool in ("text", "addtext", "shape", "stamp", "sign", "redact"):
        js("try { setTool('%s'); } catch (e) {}" % tool)
        time.sleep(0.45)
        survey = js(SURVEY.replace("SCOPE", repr("#inspector")))
        check("inspector with the %s tool" % tool, survey, "")
        if survey:
            for item in survey.split(" | "):
                print("      " + item)
    js("setTool('select')")
    time.sleep(0.4)

    print("")
    print("== removing it did not reintroduce a gap under the page ==")
    gap = js("""(function () {
        const w = document.getElementById('pagewrap').getBoundingClientRect();
        const i = document.getElementById('pageimg').getBoundingClientRect();
        return +(w.height - i.height).toFixed(2);
      })()""")
    check("page wrapper hugs the image", gap, lambda g: abs(g) < 1.5)

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF text fit", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
