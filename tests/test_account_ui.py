"""The account panel: what it says, and that it never gets in the way.

The thing most worth protecting is that signing in stays optional. Every
check below about the signed-out state is really asking the same question -
can someone who will never have an account still do everything?

Needs SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_KEY, because the
sign-in half is driven with a real emailed code fetched through the admin API.
"""
import io
import json
import os
import sys
import threading
import time
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402
os.environ["PLATENPDF_CRED_TARGET"] = "PlatenPDF:test-%s" % uuid.uuid4().hex[:8]

import webview  # noqa: E402

from backend import account, cloud, signatures  # noqa: E402
from backend.api import Api  # noqa: E402

SERVICE = os.environ.get("SUPABASE_SERVICE_KEY")
if not SERVICE:
    print("Set SUPABASE_SERVICE_KEY (and SUPABASE_URL) first.")
    raise SystemExit(2)

UI = os.path.join(ROOT, "ui", "index.html")
PDF = os.path.join(ROOT, "tests", "output", "fixture.pdf")
EMAIL = "ui-%s@example.com" % uuid.uuid4().hex[:10]
fails = []
created_user = {"id": None}


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-54s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


def admin(method, path, body=None):
    request = urllib.request.Request(
        cloud.PROJECT_URL + path,
        data=json.dumps(body).encode() if body else None,
        headers={"apikey": SERVICE, "Authorization": "Bearer " + SERVICE,
                 "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def wipe_local():
    """Start from an empty library. The sandbox folder survives between runs,
    so without this the second run of this test counts its own leftovers and
    fails on a number that was never about the feature."""
    for item in signatures._load():
        try:
            os.remove(os.path.join(signatures.SIG_DIR, item["file"]))
        except OSError:
            pass
    signatures._store([])
    try:
        os.remove(signatures.TOMBSTONES)
    except OSError:
        pass


def drive(window):
    threading.Thread(target=lambda: (time.sleep(240), print("WATCHDOG", flush=True),
                                     os._exit(2)), daemon=True).start()
    js = window.evaluate_js
    for _ in range(80):
        if js("!!(window.pywebview && window.pywebview.api) && typeof S !== 'undefined'"):
            break
        time.sleep(0.25)
    js("window.openOnStart(%s)" % repr(PDF.replace("\\", "/")))
    for _ in range(60):
        if js("S.info && S.info.page_count"):
            break
        time.sleep(0.25)
    wipe_local()
    js("document.querySelector('.tab[data-pane=\"sigs\"]')"
       " && document.querySelector('.tab[data-pane=\"sigs\"]').click()")
    js("renderAccount(); loadSigs();")
    time.sleep(2.0)

    print("== signed out: it explains, it does not nag ==")
    text = js("document.getElementById('account-strip').innerText") or ""
    check("the strip is there", bool(text.strip()), True)
    check("it says signatures live on this PC",
          "saved on this PC" in text, True)
    check("and names the exact reason to sign in",
          "use them on your phone" in text, True)
    check("there is a way in", js("!!document.getElementById('acct-in')"), True)
    check("but nothing is forced: no modal is open",
          js("document.getElementById('modal-back').classList.contains('on')"), False)

    print("")
    print("== everything still works without an account ==")
    png = js("""(function () {
        const c = document.createElement('canvas');
        c.width = 200; c.height = 70;
        const x = c.getContext('2d');
        x.fillStyle = '#fff'; x.fillRect(0, 0, 200, 70);
        x.strokeStyle = '#16205c'; x.lineWidth = 5;
        x.beginPath(); x.moveTo(15, 55); x.lineTo(90, 20); x.lineTo(185, 50); x.stroke();
        return c.toDataURL('image/png');
      })()""")
    js("window.pywebview.api.sig_add_data(%s, 'Signed Out Sig', 'Tester')" % json.dumps(png))
    time.sleep(2.0)
    js("loadSigs()")
    time.sleep(1.4)
    check("a signature can be added while signed out",
          js("document.querySelectorAll('#sig-list .sig-card').length"), 1)
    check("and it is on this machine", len(signatures._load()), 1)

    print("")
    print("== signing in ==")
    link = admin("POST", "/auth/v1/admin/generate_link",
                 {"type": "magiclink", "email": EMAIL})
    created_user["id"] = (link.get("user") or {}).get("id") or link.get("id")
    js("promptSignIn()")
    time.sleep(1.0)
    body = js("document.getElementById('modal').innerText") or ""
    check("the dialog promises a code, not a password",
          "no password" in body.lower(), True)
    check("and is explicit that documents stay put",
          "documents are never uploaded" in body.lower(), True)
    js("document.getElementById('modal-back').classList.remove('on');"
       "document.getElementById('modal').innerHTML = '';")

    # Drive the real endpoint rather than retyping the dialog.
    result = js("""(async function () {
        const r = await window.pywebview.api.account_verify_code(%s, %s);
        return JSON.stringify(r);
      })()""" % (json.dumps(EMAIL), json.dumps(link["email_otp"])))
    time.sleep(3.5)
    js("renderAccount(); loadSigs();")
    time.sleep(2.5)

    check("the session took", account.status()["signed_in"], True)
    text = js("document.getElementById('account-strip').innerText") or ""
    check("the strip now names the account", EMAIL in text, True)
    check("there is a sync button", js("!!document.getElementById('acct-sync')"), True)
    check("and a way out", js("!!document.getElementById('acct-out')"), True)

    print("")
    print("== signing in carried the existing signature up ==")
    check("it was linked to the server",
          bool(signatures._load()[0].get("remote_id")), True)
    check("and the list still shows it",
          js("document.querySelectorAll('#sig-list .sig-card').length"), 1)

    print("")
    print("== the sync button reports plainly ==")
    js("runSync(false)")
    time.sleep(4.0)
    state = js("document.getElementById('acct-state').innerText") or ""
    check("it says something afterwards", bool(state.strip()), True)
    check("and not an error", "could not" in state.lower(), False)
    print("      reported: %r" % state)

    print("")
    print("== signing out leaves the signatures alone ==")
    js("document.getElementById('acct-out').click()")
    time.sleep(2.5)
    js("renderAccount(); loadSigs();")
    time.sleep(1.8)
    check("signed out", account.status()["signed_in"], False)
    check("the signature is still here", len(signatures._load()), 1)
    check("still listed in the panel",
          js("document.querySelectorAll('#sig-list .sig-card').length"), 1)
    text = js("document.getElementById('account-strip').innerText") or ""
    check("and the invitation is back, not a lockout",
          "use them on your phone" in text, True)

    window.destroy()


api = Api()
w = webview.create_window("Platen PDF account", url=UI, js_api=api,
                          width=1250, height=900, hidden=True)
api.attach_window(w)
webview.start(drive, w)

try:
    if created_user["id"]:
        admin("DELETE", "/auth/v1/admin/users/" + created_user["id"])
except Exception:
    pass
account._clear_session()
wipe_local()

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
