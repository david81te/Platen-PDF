"""The window tells people about a newer version, and lets them act on it.

The backend could always find a newer release; for years the window never
asked, so nobody was ever told. This drives the real window: the notice shows,
its buttons do what they say, and Help > Check for updates answers truthfully.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: F401  - keeps the update state out of the real folder

import webview

from backend import updates, version
from backend.api import Api

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
fails = []


def seed(latest, dismissed=None):
    state = updates._state()
    state.update({"latest": latest, "checked": time.time(), "enabled": True,
                  "dismissed": dismissed, "notes": "**Faster things.**\n\nDetails.\n\n---\n\n"
                  "**To install:** download the zip."})
    updates._save(state)


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js

    def check(label, got, want):
        ok = want(got) if callable(want) else got == want
        if not ok:
            fails.append((label, got))
        print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)

    def wait_for(expr, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            value = js(expr)
            if value:
                return value
            time.sleep(0.2)
        return js(expr)

    for _ in range(60):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    print("== a newer version is mentioned on startup ==")
    # The startup check already ran against the seeded state (it waits 4s).
    check("the notice appears", wait_for("!!document.getElementById('updnote')", 12), True)
    check("naming the new version",
          js("document.querySelector('#updnote b').textContent"), "Platen PDF 99.0.0 is available")
    check("and the one running", js("document.querySelector('#updnote span').textContent"),
          "You have %s." % version.VERSION)
    # Run from source, so it cannot replace itself: it offers the download.
    check("a copy that cannot update itself offers the download",
          js("document.querySelector('#updnote [data-go]').textContent"), "Download")

    js("document.querySelector('#updnote [data-notes]').click()")
    time.sleep(0.5)
    notes = js("(document.querySelector('#modal .updnotes') || {}).textContent || ''")
    check("what's new shows the notes", notes, lambda t: "Faster things." in t)
    check("without markdown asterisks", "**" in notes, False)
    check("or the by-hand install footer", "To install" in notes, False)
    js("document.querySelector('#modal [data-x]').click()")

    js("document.querySelector('#updnote [data-later]').click()")
    time.sleep(0.3)
    check("later puts it away", js("!!document.getElementById('updnote')"), False)
    check("without forgetting the version", updates._state().get("dismissed"), None)

    js("showUpdateNote({latest: '99.0.0', current: %r, installable: false})" % version.VERSION)
    js("document.querySelector('#updnote [data-skip]').click()")
    time.sleep(0.6)
    check("skip this version is remembered", updates._state().get("dismissed"), "99.0.0")
    check("and the notice goes", js("!!document.getElementById('updnote')"), False)

    print("== an installed copy offers to install ==")
    js("showUpdateNote({latest: '99.0.0', current: %r, installable: true})" % version.VERSION)
    check("the button says install",
          js("document.querySelector('#updnote [data-go]').textContent"), "Install update")
    js("showUpdateNote({latest: '99.0.0', current: %r, installable: true}, true)" % version.VERSION)
    check("once downloaded it offers the restart",
          js("document.querySelector('#updnote [data-go]').textContent"), "Restart and update")
    check("with no skip on an update already fetched",
          js("!!document.querySelector('#updnote [data-skip]')"), False)
    js("document.querySelector('#updnote [data-later]').click()")

    print("== installing: one download, one question ==")
    # A stand-in download that moves through its stages on a timer.
    def fake_start():
        def advance():
            for stage, done in (("downloading", 40), ("downloading", 80), ("checking", 100),
                                ("ready", 100)):
                time.sleep(0.8)
                updates._set(stage=stage, done=done, total=100)
        updates._set(stage="downloading", done=0, total=100, version="99.0.0")
        threading.Thread(target=advance, daemon=True).start()
        return updates.progress()
    real_start = updates.start_download
    updates.start_download = fake_start
    try:
        u = "{latest: '99.0.0', current: %r, installable: true}" % version.VERSION
        js("startUpdate(%s)" % u)
        time.sleep(1.2)
        check("progress is shown", js("(document.getElementById('upd-text') || {}).textContent"),
              lambda t: t and t.startswith("Downloading"))
        # Hidden, then asked again: the same download comes back into view
        # rather than a second one starting to watch it.
        js("document.querySelector('#modal [data-x]').click()")
        js("startUpdate(%s)" % u)
        time.sleep(4.5)
        check("when ready it asks once whether to restart",
              js("document.querySelector('#modal h2') && document.querySelector('#modal h2').textContent"),
              "Ready to update")
        check("and no second copy of the question waits behind it",
              js("document.getElementById('updnote') ? document.querySelector('#updnote b').textContent : null"),
              None)
        js("document.querySelector('#modal [data-x]').click()")
        time.sleep(0.3)
    finally:
        updates.start_download = real_start
        updates._set(stage="idle")

    print("== Help > Check for updates ==")
    check("is in the Help menu",
          js("!!document.querySelector('.menu[data-menu=help] li[data-act=updates]')"), True)
    # A real check against GitHub: this tree is the released version, so it
    # must say so - and say it because GitHub answered, not from memory.
    js("ACTIONS.updates()")
    text = wait_for("(function(){const h=document.querySelector('#modal h2');"
                    "return h && h.textContent !== '' && document.querySelector('#modal p') "
                    "? h.textContent + ' | ' + document.querySelector('#modal p').textContent : ''})()",
                    30)
    check("an up-to-date copy is told so", text,
          "Check for updates | You have the newest version, %s." % version.VERSION)
    check("with the daily check switch shown on",
          js("document.querySelector('#modal input[name=auto]').checked"), True)
    js("document.querySelector('#modal input[name=auto]').click();"
       "document.querySelector('#modal [data-ok]').click()")
    time.sleep(0.8)
    check("and turning it off sticks", updates.enabled(), False)
    updates.set_enabled(True)
    window.destroy()


seed("99.0.0")
api = Api()
w = webview.create_window("Platen PDF updates", url=UI, js_api=api, width=1100, height=800)
api.attach_window(w)
webview.start(drive, w)
print("\nRESULT:", "PASS" if not fails else "FAIL %s" % fails)
sys.exit(1 if fails else 0)
