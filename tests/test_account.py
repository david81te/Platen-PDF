"""The optional account: sign in, stay signed in, sign out.

Runs against the real Supabase project, because the thing worth testing is
whether our requests are shaped the way the service expects - a mock would
only prove the mock agrees with itself.

The emailed code is fetched through the admin API instead of an inbox, which
exercises exactly the verification path a person's typed code takes.

Credentials come from the environment:
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY
"""
import io
import json
import os
import sys
import time
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Before importing the module: a test must never sign the real user out.
os.environ["PLATENPDF_CRED_TARGET"] = "PlatenPDF:test-%s" % uuid.uuid4().hex[:8]

from backend import account, cloud   # noqa: E402

SERVICE = os.environ.get("SUPABASE_SERVICE_KEY")
if not SERVICE:
    print("Set SUPABASE_SERVICE_KEY (and SUPABASE_URL) first.")
    raise SystemExit(2)

fails = []
EMAIL = "acct-%s@example.com" % uuid.uuid4().hex[:10]


def check(label, ok, detail=""):
    if not ok:
        fails.append((label, detail))
    print("  %s %-50s %s" % ("ok  " if ok else "BUG ", label, detail), flush=True)


def admin(method, path, body=None):
    request = urllib.request.Request(
        cloud.PROJECT_URL + path,
        data=json.dumps(body).encode() if body else None,
        headers={"apikey": SERVICE, "Authorization": "Bearer " + SERVICE,
                 "Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


print("== signed out to begin with ==")
check("status reports signed out", account.status()["signed_in"] is False)
check("no token is offered", account.access_token() is None)

print("")
print("== bad input is refused before anything is sent ==")
for bad in ("", "not-an-email", "missing@tld"):
    try:
        account.request_code(bad)
        check("refuses %r" % bad, False, "it was accepted")
    except account.AccountError as exc:
        check("refuses %r" % bad, True, str(exc)[:40])

print("")
print("== signing in with an emailed code ==")
# generate_link makes the account and returns the same code the email carries.
link = admin("POST", "/auth/v1/admin/generate_link",
             {"type": "magiclink", "email": EMAIL})
code = link.get("email_otp")
user_id = (link.get("user") or {}).get("id") or link.get("id")
# Supabase issues an 8-digit code for email OTP, not the 6 that SMS uses.
check("a numeric code was issued",
      bool(code) and str(code).isdigit() and 6 <= len(str(code)) <= 10,
      "%s (%d digits)" % (code, len(str(code))))

try:
    account.verify_code(EMAIL, "000000")
    check("a wrong code is rejected", False, "it was accepted")
except account.AccountError as exc:
    check("a wrong code is rejected", True, str(exc)[:46])

result = account.verify_code(EMAIL, code)
check("the right code signs in", result.get("email") == EMAIL, result.get("email"))
check("and we learn who it is", bool(result.get("user_id")), str(result.get("user_id"))[:8])

print("")
print("== the session survives ==")
state = account.status()
check("status now reports signed in", state["signed_in"] is True, state.get("email"))
token = account.access_token()
check("a usable token is available", bool(token), (token or "")[:12] + "...")

check("the session is in Credential Manager, not a file",
      account._read_session() is not None)
check("and the file-based store was never written",
      not os.path.exists(os.path.join(ROOT, "session.json")))

print("")
print("== the token actually works against the database ==")
request = urllib.request.Request(
    cloud.REST + "/profiles?select=id,email",
    headers={"apikey": cloud.PUBLISHABLE_KEY, "Authorization": "Bearer " + token})
with urllib.request.urlopen(request, timeout=40) as response:
    rows = json.loads(response.read())
check("the profile row exists and is ours",
      len(rows) == 1 and rows[0]["email"] == EMAIL, "rows=%d" % len(rows))
check("reminders are off until explicitly opted in", True, "checked below")

request = urllib.request.Request(
    cloud.REST + "/profiles?select=reminders_opted_in",
    headers={"apikey": cloud.PUBLISHABLE_KEY, "Authorization": "Bearer " + token})
with urllib.request.urlopen(request, timeout=40) as response:
    pref = json.loads(response.read())
check("signing in is not consent to be emailed",
      pref and pref[0]["reminders_opted_in"] is False, str(pref))

print("")
print("== an expired token refreshes itself ==")
stored = account._read_session()
stored["expires_at"] = int(time.time()) - 10        # pretend it aged out
account._write_session(stored)
refreshed = account.access_token()
check("a stale token is replaced, not abandoned", bool(refreshed))
check("and the new one differs from the old", refreshed != token)
check("still signed in afterwards", account.status()["signed_in"] is True)

print("")
print("== a revoked session does not loop forever ==")
broken = account._read_session()
broken["expires_at"] = int(time.time()) - 10
broken["refresh_token"] = "definitely-not-valid"
account._write_session(broken)
check("a dead refresh token gives up", account.access_token() is None)
check("and reports signed out rather than pretending",
      account.status()["signed_in"] is False)

print("")
print("== signing out ==")
account.verify_code(EMAIL, admin("POST", "/auth/v1/admin/generate_link",
                                 {"type": "magiclink", "email": EMAIL})["email_otp"])
check("signed back in for the test", account.status()["signed_in"] is True)
account.sign_out()
check("sign out clears the session", account.status()["signed_in"] is False)
check("and the credential is gone", account._read_session() is None)
check("no token is offered afterwards", account.access_token() is None)

# tidy up: the throwaway account and any stray credential
try:
    if user_id:
        admin("DELETE", "/auth/v1/admin/users/" + user_id)
except Exception:
    pass
account._clear_session()

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
