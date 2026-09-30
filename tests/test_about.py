"""The copyright must be present, consistent, and visible to a user.

Three places have to agree: backend/version.py, the Windows version resource
PyInstaller embeds in the exe, and the About box in the application. They are
separate files by necessity - PyInstaller reads its one at build time and
cannot import Python - so drift is the obvious failure and this catches it.
"""
import io
import os
import re
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webview

from backend import version
from backend.api import Api

UI = os.path.join(ROOT, "ui", "index.html")
fails = []


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-50s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


print("== the files that carry the notice ==")
resource = io.open(os.path.join(ROOT, "assets", "version_info.txt"),
                   encoding="utf-8").read()
licence = io.open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
readme = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
third = io.open(os.path.join(ROOT, "THIRD-PARTY.md"), encoding="utf-8").read()

check("LICENSE names the author", version.AUTHOR in licence, True)
check("LICENSE asserts copyright", version.COPYRIGHT in licence, True)
check("README carries the notice", version.COPYRIGHT in readme, True)
check("version resource has the same copyright",
      version.COPYRIGHT in resource, True)
check("version resource has the same author",
      "'CompanyName', '%s'" % version.AUTHOR in resource, True)
check("version resource product version matches version.py",
      "'ProductVersion', '%s'" % version.VERSION in resource, True)
found = re.search(r"filevers=\((\d+), (\d+), (\d+), \d+\)", resource)
check("version resource file version matches too",
      ".".join(found.groups()) if found else None, version.VERSION)

print("")
print("== the licences it is built on are disclosed ==")
check("THIRD-PARTY lists PyMuPDF", "PyMuPDF" in third, True)
check("and says it is AGPL", "Affero" in third, True)
check("LICENSE points at THIRD-PARTY", "THIRD-PARTY.md" in licence, True)
check("every component in the About box is documented",
      [n for n, _ in version.COMPONENTS
       if n.split(",")[0].split(" +")[0] not in third], [])


def drive(window):
    threading.Thread(target=lambda: (time.sleep(150), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)

    print("")
    print("== a user can find it: Help > About ==")
    check("there is a Help menu",
          js("!!document.querySelector('[data-menu=\"help\"]')"), True)
    check("with an About item",
          js("!!document.querySelector('li[data-act=\"about\"]')"), True)

    js("document.querySelector('li[data-act=\"about\"]').click()")
    time.sleep(2.5)
    check("the dialog opens",
          js("document.getElementById('modal-back').classList.contains('on')"), True)
    text = js("document.getElementById('modal').innerText") or ""
    check("it shows the copyright", version.COPYRIGHT in text, True)
    check("it shows the version", version.VERSION in text, True)
    check("it credits PyMuPDF", "PyMuPDF" in text, True)
    check("and names its licence", "AGPL" in text, True)
    js("document.querySelector('#modal [data-x]').click()")
    window.destroy()


api = Api()
w = webview.create_window("PDF Studio about", url=UI, js_api=api,
                          width=1100, height=800, hidden=True)
api.attach_window(w)
webview.start(drive, w)

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
