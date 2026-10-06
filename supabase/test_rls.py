"""Adversarial check: can one account reach another account's signatures?

Everything the desktop app and the phone will ever do goes through PostgREST
and storage with a user's own JWT, so this exercises the real path rather
than trusting that the policies read correctly.
"""
import json, os, sys, urllib.error, urllib.request, uuid

# Credentials come from the environment so none of them land in the repo.
#   SUPABASE_URL          https://<ref>.supabase.co
#   SUPABASE_ANON_KEY     the publishable key the apps ship with
#   SUPABASE_SERVICE_KEY  admin key, used only to create and remove test users
URL = os.environ.get("SUPABASE_URL")
ANON = os.environ.get("SUPABASE_ANON_KEY")
SERVICE = os.environ.get("SUPABASE_SERVICE_KEY")
if not (URL and ANON and SERVICE):
    print("Set SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_KEY first.")
    raise SystemExit(2)
fails = []


def call(method, path, token=None, body=None, raw=None, extra=None):
    headers = {"apikey": ANON, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if extra:
        headers.update(extra)
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            payload = r.read()
            try:
                return r.status, json.loads(payload)
            except ValueError:
                return r.status, payload
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:160]


def check(label, ok, detail=""):
    if not ok:
        fails.append(label)
    print("  %s %-52s %s" % ("ok  " if ok else "BUG ", label, detail), flush=True)


def make_user(tag):
    email = "rls-%s-%s@example.com" % (tag, uuid.uuid4().hex[:8])
    pw = uuid.uuid4().hex + "Aa1!"
    req = urllib.request.Request(
        URL + "/auth/v1/admin/users",
        data=json.dumps({"email": email, "password": pw, "email_confirm": True}).encode(),
        headers={"apikey": SERVICE, "Authorization": "Bearer " + SERVICE,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        uid = json.loads(r.read())["id"]
    status, tok = call("POST", "/auth/v1/token?grant_type=password",
                       body={"email": email, "password": pw})
    return uid, tok["access_token"], email


print("== two accounts ==")
a_id, a_tok, a_email = make_user("a")
b_id, b_tok, b_email = make_user("b")
check("account A created", bool(a_id), a_id[:8])
check("account B created", bool(b_id), b_id[:8])

status, rows = call("GET", "/rest/v1/profiles?select=id,email", a_tok)
check("A's profile row was created by the trigger",
      status == 200 and len(rows) == 1 and rows[0]["id"] == a_id, "rows=%s" % len(rows))
check("and A sees only their own profile, not B's", len(rows) == 1, "rows=%s" % len(rows))

print("")
print("== A owns a signature ==")
status, made = call("POST", "/rest/v1/signatures", a_tok,
                    body={"user_id": a_id, "name": "A signature",
                          "storage_path": "%s/sig.png" % a_id},
                    extra={"Prefer": "return=representation"})
check("A can insert their own signature", status in (200, 201), "status=%s" % status)
sig_id = made[0]["id"] if status in (200, 201) else None

png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
status, _ = call("POST", "/storage/v1/object/signatures/%s/sig.png" % a_id, a_tok,
                 raw=png, extra={"Content-Type": "image/png"})
check("A can upload their own signature image", status in (200, 201), "status=%s" % status)

print("")
print("== B goes looking ==")
status, rows = call("GET", "/rest/v1/signatures?select=*", b_tok)
check("B's own listing is empty", status == 200 and rows == [], "rows=%s" % len(rows))

status, rows = call("GET", "/rest/v1/signatures?select=*&user_id=eq.%s" % a_id, b_tok)
check("B cannot read A's signatures by filtering on A's id",
      status == 200 and rows == [], "rows=%s" % len(rows))

if sig_id:
    status, rows = call("GET", "/rest/v1/signatures?select=*&id=eq.%s" % sig_id, b_tok)
    check("B cannot fetch A's signature by its exact id",
          status == 200 and rows == [], "rows=%s" % len(rows))
    status, rows = call("PATCH", "/rest/v1/signatures?id=eq.%s" % sig_id, b_tok,
                        body={"name": "stolen"}, extra={"Prefer": "return=representation"})
    check("B cannot rename A's signature", rows == [] or rows == "", "changed=%s" % (rows or 0))
    status, rows = call("DELETE", "/rest/v1/signatures?id=eq.%s" % sig_id, b_tok,
                        extra={"Prefer": "return=representation"})
    check("B cannot delete A's signature", rows == [] or rows == "", "deleted=%s" % (rows or 0))

status, out = call("POST", "/rest/v1/signatures", b_tok,
                   body={"user_id": a_id, "name": "planted",
                         "storage_path": "%s/x.png" % a_id})
check("B cannot plant a row owned by A", status not in (200, 201), "status=%s" % status)

status, out = call("GET", "/storage/v1/object/signatures/%s/sig.png" % a_id, b_tok)
check("B cannot download A's signature image", status not in (200,), "status=%s" % status)

status, out = call("POST", "/storage/v1/object/list/signatures", b_tok,
                   body={"prefix": a_id, "limit": 100})
check("B cannot list A's storage folder",
      status != 200 or out == [], "status=%s items=%s" % (status, len(out) if isinstance(out, list) else "?"))

print("")
print("== a stranger with only the public key ==")
status, rows = call("GET", "/rest/v1/signatures?select=*")
check("anonymous reads nothing", status in (200, 401) and (rows == [] or status == 401),
      "status=%s rows=%s" % (status, len(rows) if isinstance(rows, list) else "-"))
status, rows = call("GET", "/rest/v1/profiles?select=*")
check("anonymous sees no profiles", status in (200, 401) and (rows == [] or status == 401),
      "status=%s rows=%s" % (status, len(rows) if isinstance(rows, list) else "-"))
status, out = call("GET", "/storage/v1/object/signatures/%s/sig.png" % a_id)
check("anonymous cannot fetch the image", status != 200, "status=%s" % status)

print("")
print("== and A still has everything ==")
status, rows = call("GET", "/rest/v1/signatures?select=*", a_tok)
check("A still sees their signature", status == 200 and len(rows) == 1, "rows=%s" % len(rows))
check("and it was not renamed by B",
      status == 200 and rows and rows[0]["name"] == "A signature",
      rows[0]["name"] if rows else "-")
status, out = call("GET", "/storage/v1/object/signatures/%s/sig.png" % a_id, a_tok)
check("A can still download their image", status == 200, "status=%s" % status)

# tidy up
for uid in (a_id, b_id):
    req = urllib.request.Request(URL + "/auth/v1/admin/users/" + uid,
                                 headers={"apikey": SERVICE,
                                          "Authorization": "Bearer " + SERVICE},
                                 method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=40)
    except Exception:
        pass
print("")
print("test accounts removed")
print("RESULT:", "PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
