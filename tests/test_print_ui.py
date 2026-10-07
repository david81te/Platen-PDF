"""Printing, from the window.

Nothing here sends a real job - the printing itself has its own tests. This
checks that someone can find it: a menu item where people look, Ctrl+P taken
before the browser uses it on the interface, and a dialog that explains the
page range rather than assuming.
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


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(180), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    print("== it can be found ==")
    check("there is a Print item in the File menu",
          js("!!document.querySelector('[data-menu=\"file\"] li[data-act=\"print\"]')"), True)
    check("it shows the shortcut",
          js("""(document.querySelector('li[data-act="print"] em') || {}).textContent"""),
          "Ctrl+P")
    check("the action is wired", js("typeof ACTIONS.print"), "function")

    print("")
    print("== with no document it does not pretend ==")
    js("document.querySelector('li[data-act=\"print\"]').click()")
    time.sleep(1.2)
    check("the dialog stays shut",
          js("document.getElementById('modal-back').classList.contains('on')"), False)

    print("")
    print("== with a document open ==")
    js("window.openOnStart(%s)" % repr(PDF.replace("\\", "/")))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    time.sleep(2.0)

    js("document.querySelector('li[data-act=\"print\"]').click()")
    # Waited for rather than slept through. Windows takes a couple of seconds
    # to enumerate network printers the first time, so a fixed sleep sits on
    # the boundary and fails only when the machine is busy - which is exactly
    # when a suite is running.
    opened = False
    for _ in range(60):
        if js("document.getElementById('modal-back').classList.contains('on')"):
            opened = True
            break
        time.sleep(0.25)
    check("the dialog opens", opened, True)
    body = js("document.getElementById('modal').innerText") or ""
    check("it offers a printer",
          js("!!document.querySelector('#modal select[name=\"printer\"]')"), True)
    check("with at least one to choose",
          js("""(document.querySelector('#modal select[name="printer"]') || {length: 0})
                 .length"""), lambda n: n >= 1)
    check("the default printer is preselected",
          js("""(function () {
                const s = document.querySelector('#modal select[name="printer"]');
                return !!(s && s.value);
              })()"""), True)
    check("a page box is there",
          js("!!document.querySelector('#modal input[name=\"pages\"]')"), True)
    check("and a copies box",
          js("!!document.querySelector('#modal input[name=\"copies\"]')"), True)
    check("copies starts at one",
          js("""(document.querySelector('#modal input[name="copies"]') || {}).value"""), "1")
    check("it says what an empty range means",
          "whole document" in body.lower(), True)
    check("it gives an example of the format", "1-3" in body, True)
    check("and warns that unsaved changes are included",
          "not saved" in body.lower(), True)
    check("a one-page document does not say 'All 1 pages'",
          js("""(document.querySelector('#modal input[name="pages"]') || {}).placeholder"""),
          lambda t: t == "The only page")

    print("")
    print("== the rest of the options are there ==")
    for name, label in [("subset", "odd or even"), ("orientation", "orientation"),
                        ("per_sheet", "pages per sheet"), ("duplex", "two-sided"),
                        ("scale", "fit or actual size"), ("colour", "colour")]:
        check("a %s choice" % label,
              js("!!document.querySelector('#modal select[name=\"%s\"]')" % name), True)
    for name, label in [("collate", "collate"), ("reverse", "reverse order")]:
        check("a %s tick box" % label,
              js("!!document.querySelector('#modal input[name=\"%s\"]')" % name), True)

    check("odd and even are both offered",
          js("""[...document.querySelectorAll('#modal select[name="subset"] option')]
                 .map(o => o.value).join(',')"""), "all,odd,even")
    check("collate starts ticked, which is what people expect",
          js("""(document.querySelector('#modal input[name="collate"]') || {}).checked"""), True)
    check("reverse starts unticked",
          js("""(document.querySelector('#modal input[name="reverse"]') || {}).checked"""), False)
    check("pages per sheet starts at one",
          js("""(document.querySelector('#modal select[name="per_sheet"]') || {}).value"""), "1")
    check("every sheet count offered is one the backend lays out",
          js("""[...document.querySelectorAll('#modal select[name="per_sheet"] option')]
                 .map(o => o.value).join(',')"""),
          lambda t: all(v in ("1", "2", "4", "6", "9", "16") for v in t.split(",")))
    check("orientation can follow the document",
          js("""(document.querySelector('#modal select[name="orientation"]') || {}).value"""),
          "auto")
    check("two-sided starts off",
          js("""(document.querySelector('#modal select[name="duplex"]') || {}).value"""), "none")

    print("")
    print("== options the chosen printer cannot do are turned off ==")
    # Microsoft Print to PDF cannot do either, and is on nearly every PC.
    switched = js("""(function () {
        const s = document.querySelector('#modal select[name="printer"]');
        const want = [...s.options].find(o => o.value.indexOf('Print to PDF') >= 0);
        if (!want) return 'absent';
        s.value = want.value;
        s.onchange();
        const d = document.querySelector('#modal select[name="duplex"]');
        return JSON.stringify({
          duplex_off: d.disabled,
          duplex_says_one_sided: d.value === 'none',
          explained: (document.getElementById('print-can').textContent || '').length > 0
        });
      })()""")
    if switched == "absent":
        print("  SKIP Microsoft Print to PDF is not installed on this machine")
    else:
        import json as _json
        state = _json.loads(switched)
        check("two-sided is disabled for a printer that cannot do it",
              state["duplex_off"], True)
        check("and it reads as one-sided rather than lying",
              state["duplex_says_one_sided"], True)
        check("with a line saying why", state["explained"], True)

    js("document.querySelector('#modal [data-x]').click()")
    time.sleep(0.8)

    print("")
    print("== Ctrl+P reaches it ==")
    fired = js("""(function () {
        window.__printed = false;
        const real = ACTIONS.print;
        ACTIONS.print = () => { window.__printed = true; };
        const e = new KeyboardEvent('keydown', {key: 'p', ctrlKey: true,
                                                bubbles: true, cancelable: true});
        document.dispatchEvent(e);
        const stopped = e.defaultPrevented;
        ACTIONS.print = real;
        return JSON.stringify({ called: window.__printed, stopped: stopped });
      })()""")
    import json
    outcome = json.loads(fired)
    check("Ctrl+P calls print", outcome["called"], True)
    check("and stops the browser printing the interface", outcome["stopped"], True)

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF print", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
