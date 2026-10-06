"""Signature sync, driven the way two devices would drive it.

Runs against the real Supabase project with a throwaway account. A second
device is simulated by emptying the local library while staying signed in -
which is exactly what a fresh machine looks like to the server.

What is deliberately checked: that a deletion travels, and that the signature
does not come straight back on the next sync. That is the failure that makes
sync feel haunted, and it is invisible until someone deletes something twice.

    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY
"""
import io
import json
import os
import sys
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import sandbox  # noqa: F401,E402  - its own signature folder
os.environ["PLATENPDF_CRED_TARGET"] = "PlatenPDF:test-%s" % uuid.uuid4().hex[:8]

from backend import account, cloud, signatures, sync  # noqa: E402

SERVICE = os.environ.get("SUPABASE_SERVICE_KEY")
if not SERVICE:
    print("Set SUPABASE_SERVICE_KEY (and SUPABASE_URL) first.")
    raise SystemExit(2)

fails = []
EMAIL = "sync-%s@example.com" % uuid.uuid4().hex[:10]
def _png() -> bytes:
    """A real signature-ish image. Hand-assembled PNG bytes are not worth the
    trouble - the first attempt here was malformed and Pillow rejected it."""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (260, 90), "white")
    ImageDraw.Draw(canvas).line([(20, 70), (90, 25), (150, 65), (235, 28)],
                                fill=(14, 22, 92), width=6)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


def check(label, got, want):
    ok = want(got) if callable(want) else got == want
    if not ok:
        fails.append((label, got))
    print("  %s %-52s %r" % ("ok  " if ok else "BUG ", label, got), flush=True)


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
    """What a second, freshly installed machine looks like."""
    for item in signatures._load():
        try:
            os.remove(os.path.join(signatures.SIG_DIR, item["file"]))
        except OSError:
            pass
    signatures._store([])
    # Clear tombstones too: a new machine has never deleted anything.
    try:
        os.remove(signatures.TOMBSTONES)
    except OSError:
        pass


def remote_rows(include_deleted=True):
    token = account.access_token()
    return [r for r in sync._remote_rows(token)
            if include_deleted or not r.get("deleted_at")]


print("== signed out, sync is a no-op rather than an error ==")
state = sync.sync()
check("it does not raise", state.get("signed_in"), False)
check("and it says what to do", "Sign in" in (state.get("message") or ""), True)

link = admin("POST", "/auth/v1/admin/generate_link",
             {"type": "magiclink", "email": EMAIL})
user_id = (link.get("user") or {}).get("id") or link.get("id")
account.verify_code(EMAIL, link["email_otp"])
check("signed in for the rest of this", account.status()["signed_in"], True)

print("")
print("== device one: a new signature goes up ==")
wipe_local()
import base64
made = signatures.add("data:image/png;base64," + base64.b64encode(PNG).decode(),
                      "Alex Morgan", "Director", drop_background=False)
check("it exists locally", len(signatures._load()), 1)
check("with no remote id yet", signatures._load()[0].get("remote_id"), None)

report = sync.sync()
check("sync reported no problems", report["problems"], [])
check("one upload", report["uploaded"], 1)
check("it now has a remote id", bool(signatures._load()[0].get("remote_id")), True)
rows = remote_rows(include_deleted=False)
check("the server has exactly one", len(rows), 1)
check("with the right name", rows[0]["name"], "Alex Morgan")
check("and the role came with it", rows[0]["role"], "Director")
check("the image path points at our own folder",
      rows[0]["storage_path"].startswith(user_id + "/"), True)

print("")
print("== syncing again changes nothing ==")
again = sync.sync()
check("no second upload", again["uploaded"], 0)
check("no download either", again["downloaded"], 0)
check("still one on the server", len(remote_rows(include_deleted=False)), 1)

print("")
print("== device two: an empty machine pulls it down ==")
wipe_local()
check("nothing here to begin with", len(signatures._load()), 0)
report = sync.sync()
check("sync reported no problems", report["problems"], [])
check("one download", report["downloaded"], 1)
local = signatures._load()
check("the signature arrived", len(local), 1)
check("with its name", local[0]["name"], "Alex Morgan")
check("and its role", local[0]["role"], "Director")
check("linked to the same remote row", local[0]["remote_id"], rows[0]["id"])
check("and the image file is really there",
      os.path.isfile(os.path.join(signatures.SIG_DIR, local[0]["file"])), True)

print("")
print("== a rename here reaches the server ==")
signatures.rename(local[0]["id"], "Alex Morgan-Reid", "Managing Director")
report = sync.sync()
check("sync reported no problems", report["problems"], [])
check("the rename was pushed", report["renamed_there"], 1)
rows = remote_rows(include_deleted=False)
check("the server has the new name", rows[0]["name"], "Alex Morgan-Reid")
check("and the new role", rows[0]["role"], "Managing Director")

print("")
print("== a rename on the server reaches here ==")
token = account.access_token()
sync._call("PATCH", cloud.REST + "/signatures?id=eq." + rows[0]["id"], token,
           body={"name": "Alexandra Morgan-Reid"})
report = sync.sync()
check("sync reported no problems", report["problems"], [])
check("the rename was pulled", report["renamed_here"], 1)
check("the local name followed", signatures._load()[0]["name"], "Alexandra Morgan-Reid")

print("")
print("== deleting here removes it there ==")
signatures.remove(signatures._load()[0]["id"])
check("gone locally", len(signatures._load()), 0)
check("a tombstone was left so it can travel", len(signatures.tombstones()), 1)
report = sync.sync()
check("sync reported no problems", report["problems"], [])
check("the deletion was pushed", report["removed_there"], 1)
check("the server shows none live", len(remote_rows(include_deleted=False)), 0)
check("the tombstone was cleared once delivered", len(signatures.tombstones()), 0)

print("")
print("== and it does not come back ==")
report = sync.sync()
check("nothing downloaded", report["downloaded"], 0)
check("still empty here", len(signatures._load()), 0)
wipe_local()
report = sync.sync()
check("a fresh machine does not resurrect it", report["downloaded"], 0)
check("which is the whole point", len(signatures._load()), 0)

print("")
print("== two edits in the same second still resolve ==")
# This is the case that caught it. Truncating both timestamps to whole
# seconds made them compare equal, so the later edit was silently dropped and
# the test only failed once the machine was fast enough to do both inside one
# second. Everything here happens as quickly as possible on purpose.
from backend.sync import _moment  # noqa: E402

check("a later fraction beats an earlier one in the same second",
      _moment("2026-01-01T00:00:00.900000+00:00")
      > _moment("2026-01-01T00:00:00.100000+00:00"), True)
check("a bare Z does not win a tie by sorting",
      _moment("2026-01-01T00:00:00.500000+00:00")
      > _moment("2026-01-01T00:00:00Z"), True)
check("an unreadable stamp loses rather than throws",
      _moment("nonsense") < _moment("2026-01-01T00:00:00Z"), True)

wipe_local()
quick = signatures.add("data:image/png;base64," + base64.b64encode(PNG).decode(),
                       "Same Second", drop_background=False)
sync.sync()
rid_fast = signatures._load()[0]["remote_id"]
token = account.access_token()
sync._call("PATCH", cloud.REST + "/signatures?id=eq." + rid_fast, token,
           body={"name": "Renamed On The Server"})
report = sync.sync()
check("a rename moments after the upload is still pulled", report["renamed_here"], 1)
check("and the name followed", signatures._load()[0]["name"], "Renamed On The Server")

print("")
print("== a deletion on the server removes it here ==")
# Start this section from nothing on either side, so it is not counting
# signatures left behind by the section above.
for row in remote_rows(include_deleted=False):
    sync._call("PATCH", cloud.REST + "/signatures?id=eq." + row["id"],
               account.access_token(), body={"deleted_at": "2030-01-01T00:00:00Z"})
wipe_local()
sync.sync()
check("both sides empty to begin with", len(signatures._load()), 0)

signatures.add("data:image/png;base64," + base64.b64encode(PNG).decode(),
               "Temporary", drop_background=False)
sync.sync()
mine = [i for i in signatures._load() if i["name"] == "Temporary"]
check("the temporary one is here and linked", len(mine) == 1 and bool(mine[0]["remote_id"]), True)
rid = mine[0]["remote_id"]
token = account.access_token()
sync._call("PATCH", cloud.REST + "/signatures?id=eq." + rid, token,
           body={"deleted_at": "2030-01-01T00:00:00Z"})
report = sync.sync()
check("the removal was pulled", report["removed_here"], 1)
check("gone from this machine",
      [i for i in signatures._load() if i["name"] == "Temporary"], [])
check("and no tombstone was invented for it", len(signatures.tombstones()), 0)

# tidy up
try:
    if user_id:
        admin("DELETE", "/auth/v1/admin/users/" + user_id)
except Exception:
    pass
account._clear_session()
wipe_local()

print("")
print("RESULT:", "PASS" if not fails else "FAIL")
for item in fails:
    print("  -", item)
sys.exit(1 if fails else 0)
