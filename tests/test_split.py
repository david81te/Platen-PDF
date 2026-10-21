"""Two documents side by side, and dragging tabs between the panes.

The window can be split in two. Only the pane in front carries the tools, so
the thing to prove is that the other one really is showing a different
document at the same time - and that an edit still lands in the pane the
person is looking at, not in whichever document happened to be active before.
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

import sandbox  # noqa: E402

import pymupdf as fitz  # noqa: E402
import webview  # noqa: E402

from backend.api import Api  # noqa: E402

UI = os.path.join(ROOT, "ui", "index.html")
LEFT = os.path.join(sandbox.DIR, "left.pdf")
RIGHT = os.path.join(sandbox.DIR, "right.pdf")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def build(path, word, pages):
    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_text(fitz.Point(40, 80), "%s %d" % (word, n + 1), fontsize=22)
    doc.save(path)
    doc.close()


build(LEFT, "LEFT", 3)
build(RIGHT, "RIGHT", 5)

print("== the backend can hand out two documents at once ==")
api = Api()
api.open_path(LEFT)
api.open_path(RIGHT)
first = api.doc_info(tab=0)["data"]
second = api.doc_info(tab=1)["data"]
check("two documents are open", [first["name"], second["name"]],
      ["left.pdf", "right.pdf"])
check("with different page counts", [first["page_count"], second["page_count"]], [3, 5])
check("each renders on request",
      [bool(api.render(0, 0.4, tab=t)["data"]["image"]) for t in (0, 1)], [True, True])
check("and asking for one does not change which is active",
      api.tab_list()["data"]["active"], 1)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(240), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    js("window.openOnStart(%r)" % LEFT.replace("\\", "/"))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%r)" % RIGHT.replace("\\", "/"))
    time.sleep(3.0)

    print("")
    print("== to begin with there is one pane ==")
    check("one pane on screen", js("document.querySelectorAll('.vpane').length"), 1)
    check("holding both tabs", js("PANES[0].tabs.length"), 2)
    check("the tab bar groups them", js("document.querySelectorAll('.tabgroup').length"), 1)
    check("two tabs are shown", js("document.querySelectorAll('.dtab').length"), 2)

    print("")
    print("== dropping a tab on the right splits the window ==")
    js("dropTab(0, 'right')")
    time.sleep(3.5)
    check("there are two panes now", js("document.querySelectorAll('.vpane').length"), 2)
    check("the dragged document went to the new side",
          js("PANES[1].tabs.join(',')"), "0")
    check("and the other side kept the rest",
          js("PANES[0].tabs.join(',')"), "1")
    check("the tab bar shows two groups",
          js("document.querySelectorAll('.tabgroup').length"), 2)

    print("")
    print("== both documents really are on screen at once ==")
    check("both panes are showing a page",
          js("document.querySelectorAll('.vpane .stage.on').length"), 2)
    check("both have a page image with something in it",
          js("""[...document.querySelectorAll('.vpane .pageimg')]
                 .filter(i => i.src && i.src.length > 100).length"""), 2)
    check("and they are different documents",
          js("""(function () {
                const a = PANES[0].state.info, b = PANES[1].state.info;
                return a && b ? a.name + '|' + b.name : null;
              })()"""), "right.pdf|left.pdf")
    check("with their own page counts",
          js("PANES[0].state.info.page_count + ',' + PANES[1].state.info.page_count"),
          "5,3")
    check("the two images are not the same picture",
          js("""(function () {
                const i = [...document.querySelectorAll('.vpane .pageimg')];
                return i.length === 2 && i[0].src !== i[1].src;
              })()"""), True)

    print("")
    print("== only one pane carries the tools ==")
    check("exactly one pane is in front",
          js("document.querySelectorAll('.vpane.focused').length"), 1)
    check("S is that pane's own state",
          js("S === PANES[focusedPane].state"), True)
    js("window.__active = null;"
       "(async () => { window.__active = (await run('tab_list')).active; })()")
    time.sleep(1.5)
    check("and the backend is on that pane's document",
          js("window.__active"), js("PANES[focusedPane].tab"))

    print("")
    print("== each pane keeps its own place in its own document ==")
    js("PANES[0].state.page = 3;")
    js("focusPane(0)")
    time.sleep(3.0)
    check("moving focus keeps the page it was on", js("S.page"), 3)
    check("and the other pane still remembers its own",
          js("PANES[1].state.page"), 0)

    print("")
    print("== an edit goes to the pane in front ==")
    # Focus is on the pane showing right.pdf (5 pages). Rotating must change
    # that document, not the one in the other pane.
    before = js("PANES[1].state.info.page_count")
    js("(async () => { await run('page_insert', 0, 'letter');"
       "                 setInfo(await run('doc_info')); })()")
    time.sleep(3.5)
    check("the pane in front gained a page", js("S.info.page_count"), 6)
    check("the other document was left alone",
          js("PANES[1].state.info.page_count"), before)

    print("")
    print("== dragging it back to the other side closes the split ==")
    js("dropTab(0, {pane: 0})")
    time.sleep(3.5)
    check("one pane again", js("document.querySelectorAll('.vpane').length"), 1)
    check("holding both tabs once more", js("PANES[0].tabs.length"), 2)
    check("and only one tab group", js("document.querySelectorAll('.tabgroup').length"), 1)
    check("the surviving pane is in front",
          js("document.querySelectorAll('.vpane.focused').length"), 1)
    check("and it is still usable",
          js("!!(S.info && S.info.page_count)"), True)

    print("")
    print("== the drop target is worked out from where the pointer is ==")
    check("the far left offers a split when there is one pane",
          js("""(function () {
                const b = document.getElementById('panes').getBoundingClientRect();
                return dropTargetAt(b.left + 10, b.top + b.height / 2);
              })()"""), "left")
    check("the far right as well",
          js("""(function () {
                const b = document.getElementById('panes').getBoundingClientRect();
                return dropTargetAt(b.right - 10, b.top + b.height / 2);
              })()"""), "right")
    check("the middle offers nothing, so a careless drag does nothing",
          js("""(function () {
                const b = document.getElementById('panes').getBoundingClientRect();
                return dropTargetAt(b.left + b.width / 2, b.top + b.height / 2);
              })()"""), None)
    check("and nothing above the panes either",
          js("""(function () {
                const b = document.getElementById('panes').getBoundingClientRect();
                return dropTargetAt(b.left + b.width / 2, b.top - 40);
              })()"""), None)

    window.destroy()


api2 = Api()
w = webview.create_window("Platen PDF split", url=UI, js_api=api2,
                          width=1400, height=900, hidden=True)
api2.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
