"""Send email via the Hermes Google OAuth token (gmail.send scope).

No googleapiclient dependency: Credentials refresh + one REST call.
Only read-side effect is refreshing the access token in memory.
"""

from __future__ import annotations

import base64
import json
from email.message import EmailMessage
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

TOKEN_PATH = Path.home() / ".hermes" / "google_token.json"
_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _credentials() -> tuple[Credentials, str]:
    info = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info)
    if not creds.valid:
        creds.refresh(Request())
    account = info.get("account") or ""
    return creds, account


def sender_address() -> str:
    """Account email the token belongs to ('' when unavailable)."""
    try:
        creds, account = _credentials()
        if "@" in (account or ""):
            return account
        # Token files written by Hermes may omit "account" — ask Gmail directly.
        resp = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("emailAddress", "")
    except Exception:
        return ""


def token_ready() -> bool:
    """True when the stored OAuth token can produce an access token."""
    if not TOKEN_PATH.exists():
        return False
    try:
        _credentials()
        return True
    except Exception:
        return False


def send_email(to: str, subject: str, body: str) -> dict:
    """Send one plain-text email. Raises on failure (never silent)."""
    creds, sender = _credentials()
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender or "me"
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    resp = requests.post(
        _SEND_URL,
        headers={"Authorization": f"Bearer {creds.token}"},
        json={"raw": raw},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data.get("id", ""), "from": sender}