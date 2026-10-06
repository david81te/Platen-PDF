"""The optional account.

Signing in is never required. Every feature of Platen PDF works signed out,
with signatures held on this machine; the account exists so the same
signatures appear on the phone, and so there is somewhere to ask about a
donation. Documents are never uploaded - only signature images.

Sign-in is a six-digit code sent by email. No password is ever chosen, stored
or transmitted, which removes the whole category of problems that come with
holding one.

The session is kept in Windows Credential Manager rather than a file beside
the program. A refresh token in a plain file is readable by anything running
as the user, including whatever they downloaded last week; Credential Manager
is encrypted with the user's own login and is the right place for it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import cloud

# PLATENPDF_CRED_TARGET moves the stored session somewhere else. It exists for
# the tests: without it they sign in and out of the real Credential Manager
# entry belonging to whoever is running them, and a test that dies before its
# cleanup leaves a person signed out of their own account.
CRED_TARGET = os.environ.get("PLATENPDF_CRED_TARGET") or "PlatenPDF:account"
# Refresh a little early: a token that expires mid-request is a failure the
# user sees, and the cost of refreshing slightly sooner is nothing.
REFRESH_MARGIN = 120


class AccountError(Exception):
    """Something the user needs told about, in words they can act on."""


# --------------------------------------------------------------- transport
def _request(method: str, url: str, body: dict | None = None,
             token: str | None = None, timeout: int = 30) -> dict:
    headers = {
        "apikey": cloud.PUBLISHABLE_KEY,
        "Content-Type": "application/json",
    }
    headers["Authorization"] = "Bearer " + (token or cloud.PUBLISHABLE_KEY)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise AccountError(_readable(exc)) from exc
    except urllib.error.URLError as exc:
        raise AccountError(
            "Could not reach the sign-in service. Check your internet "
            "connection and try again.") from exc


def _readable(exc: urllib.error.HTTPError) -> str:
    """Turn a server error into something worth showing a person."""
    try:
        detail = json.loads(exc.read())
    except Exception:
        detail = {}
    code = detail.get("error_code") or detail.get("code")
    message = detail.get("msg") or detail.get("message") or ""

    if code == "otp_expired" or "expired" in message.lower():
        # Supabase answers "expired" for a wrong code as well as a stale one,
        # deliberately, so that a stranger cannot probe which emails exist.
        # Saying only "expired" would send someone who simply mistyped off to
        # request another code they do not need.
        return ("That code was not accepted - it may be mistyped, or it may "
                "have expired. Check it, or ask for a new one.")
    if code in ("invalid_credentials", "otp_disabled") or exc.code in (400, 401, 403):
        if "token" in message.lower() or "otp" in str(code):
            return "That code was not right. Check it and try again."
    if exc.code == 429:
        return ("Too many sign-in emails for the moment. Wait a few minutes "
                "and try again.")
    return message or "The sign-in service returned an error (%s)." % exc.code


# ------------------------------------------------------- session on disk
def _write_session(session: dict) -> None:
    import win32cred
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": CRED_TARGET,
        "UserName": session.get("email") or "platen",
        "CredentialBlob": json.dumps(session),
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        "Comment": "Platen PDF signature sync",
    }, 0)


def _read_session() -> dict | None:
    try:
        import win32cred
        entry = win32cred.CredRead(CRED_TARGET, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        return None
    blob = entry.get("CredentialBlob")
    if not blob:
        return None
    try:
        # pywin32 hands back the blob as bytes; it was stored as UTF-16.
        text = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
        return json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return None


def _clear_session() -> None:
    try:
        import win32cred
        win32cred.CredDelete(CRED_TARGET, win32cred.CRED_TYPE_GENERIC)
    except Exception:
        pass


def _store(payload: dict, email: str) -> dict:
    user = payload.get("user") or {}
    session = {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "expires_at": int(time.time()) + int(payload.get("expires_in") or 3600),
        "user_id": user.get("id"),
        "email": user.get("email") or email,
    }
    _write_session(session)
    return session


# ------------------------------------------------------------ public API
def request_code(email: str) -> dict:
    """Email a six-digit sign-in code, creating the account if it is new."""
    email = (email or "").strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AccountError("That does not look like an email address.")
    _request("POST", cloud.AUTH + "/otp",
             {"email": email, "create_user": True})
    return {"sent_to": email}


def verify_code(email: str, code: str) -> dict:
    """Exchange the emailed code for a session."""
    email = (email or "").strip()
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code:
        raise AccountError("Enter the code from the email.")
    payload = _request("POST", cloud.AUTH + "/verify",
                       {"email": email, "token": code, "type": "email"})
    if not payload.get("access_token"):
        raise AccountError("That code was not right. Check it and try again.")
    session = _store(payload, email)
    return {"email": session["email"], "user_id": session["user_id"]}


def access_token() -> str | None:
    """A usable token, refreshed if it is close to expiry. None if signed out."""
    session = _read_session()
    if not session or not session.get("refresh_token"):
        return None
    if session.get("access_token") and \
            session.get("expires_at", 0) - REFRESH_MARGIN > time.time():
        return session["access_token"]
    try:
        payload = _request("POST", cloud.AUTH + "/token?grant_type=refresh_token",
                           {"refresh_token": session["refresh_token"]})
    except AccountError:
        # The refresh token has been revoked or has aged out. Signed out is the
        # honest state; silently retrying forever would just look broken.
        _clear_session()
        return None
    if not payload.get("access_token"):
        _clear_session()
        return None
    return _store(payload, session.get("email", ""))["access_token"]


def status() -> dict:
    """Who is signed in, if anyone. Never raises - the UI asks this constantly."""
    session = _read_session()
    if not session:
        return {"signed_in": False, "email": None, "user_id": None}
    return {
        "signed_in": True,
        "email": session.get("email"),
        "user_id": session.get("user_id"),
        # Stale is not signed out: the token refreshes on next use.
        "stale": session.get("expires_at", 0) <= time.time(),
    }


def sign_out() -> dict:
    """Forget the session on this machine. Signatures already here stay here."""
    token = None
    session = _read_session()
    if session:
        token = session.get("access_token")
    if token:
        try:
            _request("POST", cloud.AUTH + "/logout", {}, token=token)
        except AccountError:
            pass        # the local session goes either way
    _clear_session()
    return {"signed_in": False}
