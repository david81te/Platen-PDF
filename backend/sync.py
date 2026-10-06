"""Keeping one person's signatures the same on their computer and their phone.

Only signatures travel. Documents never leave the machine they are on, which
is the whole point of the program and is not negotiable for a sync feature.

The rules, in the order they matter:

* Nothing is ever destroyed to resolve a disagreement. Where two sides have
  edited the same signature, the later edit wins for the name, but no image is
  thrown away.
* A deletion must be able to travel. Removing a signature here leaves a
  tombstone; without one the next sync would see it missing locally, decide it
  was new on the server, and download it straight back.
* Signed out is a perfectly good state. Nothing here runs unless someone has
  chosen to sign in.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

from . import account, cloud, signatures


def _moment(text: str | None) -> datetime:
    """Read either side's timestamp into something comparable.

    Postgres hands back 2026-10-06T21:15:27.806895+00:00 and this program
    writes 2026-10-06T21:15:27.806895+00:00, but older local records end in a
    plain Z and some have no fraction at all. Comparing the strings was the
    original mistake: it made two edits in the same second indistinguishable,
    and a bare Z sorts after a decimal point, so the local copy won every tie
    regardless of which was actually newer.
    """
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class SyncError(Exception):
    """Something worth telling the user, in words they can act on."""


def _call(method: str, url: str, token: str, body=None, raw: bytes | None = None,
          extra: dict | None = None, timeout: int = 45):
    headers = {"apikey": cloud.PUBLISHABLE_KEY, "Authorization": "Bearer " + token}
    if raw is None:
        headers["Content-Type"] = "application/json"
    if extra:
        headers.update(extra)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            if not payload:
                return None
            if response.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(payload)
            return payload
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise SyncError("The server refused a request (%s): %s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise SyncError("Could not reach the sync service. Check your "
                        "internet connection.") from exc


def _remote_rows(token: str) -> list[dict]:
    rows = _call("GET", cloud.REST + "/signatures?select=*", token)
    return rows or []


def _object_path(user_id: str, remote_id: str) -> str:
    return "%s/%s.png" % (user_id, remote_id)


def _upload(token: str, user_id: str, remote_id: str, data: bytes) -> str:
    path = _object_path(user_id, remote_id)
    url = "%s/object/%s/%s" % (cloud.STORAGE, cloud.SIGNATURE_BUCKET,
                               urllib.parse.quote(path))
    # x-upsert so a re-run after a half-finished sync replaces rather than fails
    _call("POST", url, token, raw=data,
          extra={"Content-Type": "image/png", "x-upsert": "true"})
    return path


def _download(token: str, path: str) -> bytes:
    url = "%s/object/%s/%s" % (cloud.STORAGE, cloud.SIGNATURE_BUCKET,
                               urllib.parse.quote(path))
    data = _call("GET", url, token)
    if not isinstance(data, (bytes, bytearray)):
        raise SyncError("The signature image came back in an unexpected form.")
    return bytes(data)


def _local_by_remote() -> dict:
    return {item["remote_id"]: item for item in signatures._load()
            if item.get("remote_id")}


def status() -> dict:
    """Whether syncing is even possible, without doing any of it."""
    who = account.status()
    if not who["signed_in"]:
        return {"signed_in": False, "local": len(signatures._load())}
    return {"signed_in": True, "email": who.get("email"),
            "local": len(signatures._load())}


def sync() -> dict:
    """Make both sides match. Safe to run again if it fails part way."""
    token = account.access_token()
    if not token:
        return {"signed_in": False, "message":
                "Sign in to use these signatures on your phone."}
    who = account.status()
    user_id = who.get("user_id")
    if not user_id:
        return {"signed_in": False, "message": "Sign in again to sync."}

    report = {"signed_in": True, "uploaded": 0, "downloaded": 0,
              "renamed_here": 0, "renamed_there": 0,
              "removed_here": 0, "removed_there": 0, "problems": []}

    remote = {row["id"]: row for row in _remote_rows(token)}

    # 1. Deletions made here, pushed out before anything else so a signature
    #    deleted locally is not re-downloaded by the pull below.
    for mark in signatures.tombstones():
        rid = mark.get("remote_id")
        if rid and rid in remote and not remote[rid].get("deleted_at"):
            try:
                _call("PATCH", cloud.REST + "/signatures?id=eq." + rid, token,
                      body={"deleted_at": mark.get("at")})
                remote[rid]["deleted_at"] = mark.get("at")
                report["removed_there"] += 1
            except SyncError as exc:
                report["problems"].append(str(exc))
                continue
        signatures.forget_tombstone(rid)

    local = signatures._load()
    linked = _local_by_remote()

    # 2. Anything here that the server has never seen.
    for item in local:
        if item.get("remote_id"):
            continue
        try:
            with open(os.path.join(signatures.SIG_DIR, item["file"]), "rb") as fh:
                data = fh.read()
        except OSError:
            report["problems"].append("Missing image for %r" % item.get("name"))
            continue
        try:
            created = _call("POST", cloud.REST + "/signatures", token,
                            body={"user_id": user_id, "name": item["name"],
                                  "role": item.get("role") or None,
                                  "storage_path": "pending"},
                            extra={"Prefer": "return=representation"})
            remote_id = created[0]["id"]
            path = _upload(token, user_id, remote_id, data)
            _call("PATCH", cloud.REST + "/signatures?id=eq." + remote_id, token,
                  body={"storage_path": path})
        except (SyncError, KeyError, IndexError, TypeError) as exc:
            report["problems"].append("Could not upload %r: %s" % (item.get("name"), exc))
            continue
        item["remote_id"] = remote_id
        report["uploaded"] += 1
    signatures._store(local)

    # 3. Anything on the server that is not here, or that changed there.
    for rid, row in remote.items():
        here = linked.get(rid)
        if row.get("deleted_at"):
            if here:
                # Removed on the other device. Drop it without leaving a
                # tombstone, or we would push the deletion back as our own.
                remaining = [i for i in signatures._load() if i.get("remote_id") != rid]
                try:
                    os.remove(os.path.join(signatures.SIG_DIR, here["file"]))
                except OSError:
                    pass
                signatures._store(remaining)
                report["removed_here"] += 1
            continue

        if here is None:
            try:
                data = _download(token, row["storage_path"])
            except SyncError as exc:
                report["problems"].append("Could not download %r: %s" % (row.get("name"), exc))
                continue
            entry = signatures.add(
                "data:image/png;base64," + _b64(data),
                row.get("name") or "Signature", row.get("role") or "",
                drop_background=False)       # already cleaned when first added
            items = signatures._load()
            for item in items:
                if item["id"] == entry["id"]:
                    item["remote_id"] = rid
                    item["updated"] = row.get("updated_at") or item["updated"]
            signatures._store(items)
            report["downloaded"] += 1
            continue

        # Both sides have it: the later edit decides the name.
        theirs = _moment(row.get("updated_at"))
        ours = _moment(here.get("updated"))
        differs = (row.get("name") != here.get("name")
                   or (row.get("role") or "") != (here.get("role") or ""))
        if theirs > ours and differs:
            items = signatures._load()
            for item in items:
                if item.get("remote_id") == rid:
                    item["name"] = row.get("name") or item["name"]
                    item["role"] = row.get("role") or ""
                    item["updated"] = row.get("updated_at")
            signatures._store(items)
            report["renamed_here"] += 1
        elif ours > theirs and differs:
            try:
                sent = _call("PATCH", cloud.REST + "/signatures?id=eq." + rid, token,
                             body={"name": here["name"],
                                   "role": here.get("role") or None},
                             extra={"Prefer": "return=representation"})
                # Adopt the server's timestamp for the row we just wrote, so
                # both sides agree on when it last changed. Leaving ours behind
                # makes every later comparison answer from stale information.
                if sent:
                    items = signatures._load()
                    for item in items:
                        if item.get("remote_id") == rid:
                            item["updated"] = sent[0].get("updated_at") or item["updated"]
                    signatures._store(items)
                report["renamed_there"] += 1
            except SyncError as exc:
                report["problems"].append(str(exc))

    report["local"] = len(signatures._load())
    return report


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()
