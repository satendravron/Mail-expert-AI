"""
Gmail Connector for Mail Expert AI.

What this does, step by step:
  1. Opens a browser window ONCE so you can log in and grant read-only
     Gmail access (uses `credentials.json` you downloaded from Google Cloud).
  2. Saves a `token.json` after that first login, so future runs DON'T
     need you to log in again (until the token expires/is revoked).
  3. Pulls your N most recent emails from your inbox.
  4. Runs each one through the SAME classify_email() engine you already
     tested via /docs — no duplicate logic, just a different mail source.
  5. Prints them sorted High → Medium → Low, right in your terminal.

Run:
    python gmail_connector.py

Requirements (already installed if you ran requirements.txt + the extra
Google libs):
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
"""

from __future__ import annotations
import os
import sys
import base64
import re
from datetime import datetime
from typing import List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models import Email, Preferences
from importance_engine import classify_email
import db

# Read-only scope: this app can NEVER send, delete, or modify your mail —
# only read it. This matches the "opt-in, read-only" privacy stance from
# the product plan.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
MAX_EMAILS_TO_FETCH = 15


def is_gmail_authenticated() -> tuple[bool, str | None]:
    """Checks if token.json exists and credentials are valid/refreshable.
    Returns (is_auth, user_email)."""
    if not os.path.exists(TOKEN_FILE):
        return False, None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())
            except Exception:
                return False, None

        if creds and creds.valid:
            service = build("gmail", "v1", credentials=creds)
            try:
                profile = service.users().getProfile(userId="me").execute()
                email = profile.get("emailAddress", "Connected Account")
                return True, email
            except Exception:
                return True, "Connected Account"
    except Exception:
        return False, None

    return False, None


def logout_gmail() -> bool:
    """Logs out of Gmail by removing the token.json file."""
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
            return True
        except Exception as e:
            print(f"[warn] Could not remove token file: {e}")
            return False
    return True



_LAST_CODE_VERIFIER: str | None = None


def get_authorization_url(redirect_uri: str) -> str:
    """Generates a Google OAuth authorization URL suitable for mobile or desktop browser login."""
    global _LAST_CODE_VERIFIER
    from google_auth_oauthlib.flow import Flow
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"Couldn't find '{CREDENTIALS_FILE}' in this folder. "
            "Download client credentials from Google Cloud Console."
        )
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="select_account consent",
        include_granted_scopes="true"
    )
    _LAST_CODE_VERIFIER = getattr(flow, "code_verifier", None)
    return auth_url


def exchange_code_for_token(code: str, redirect_uri: str):
    """Exchanges OAuth code from web redirect into credentials and persists token.json."""
    global _LAST_CODE_VERIFIER
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    if _LAST_CODE_VERIFIER:
        flow.code_verifier = _LAST_CODE_VERIFIER
    flow.fetch_token(code=code)
    creds = flow.credentials
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())
    return creds


def get_gmail_service(allow_local_server: bool = False):
    """Handles the OAuth dance. Server mode raises error if auth needed; CLI mode launches browser."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Couldn't find '{CREDENTIALS_FILE}' in this folder. "
                    "Make sure you downloaded it from Google Cloud Console "
                    "and renamed it to exactly 'credentials.json'."
                )
            if allow_local_server:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)  # opens the browser login window
            else:
                raise RuntimeError("Gmail authentication required. Please connect via OAuth URL.")

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    """Gmail bodies are base64url-encoded and can be nested in multipart mime.
    This walks the structure to find the plain-text part."""
    def find_text(part):
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
        for sub in part.get("parts", []) or []:
            found = find_text(sub)
            if found:
                return found
        return None

    text = find_text(payload)
    if text:
        return text

    # Fallback: strip HTML tags crudely if only an HTML part exists.
    def find_html(part):
        if part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
        for sub in part.get("parts", []) or []:
            found = find_html(sub)
            if found:
                return found
        return None

    html = find_html(payload)
    if html:
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _get_header(headers: List[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def fetch_recent_emails(service, user_id: str, max_results: int = MAX_EMAILS_TO_FETCH) -> List[Email]:
    """Pulls the most recent inbox messages and converts them into our Email model."""
    results = service.users().messages().list(
        userId="me", labelIds=["INBOX"], maxResults=max_results
    ).execute()
    message_refs = results.get("messages", [])

    emails: List[Email] = []
    for ref in message_refs:
        msg = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        subject = _get_header(headers, "Subject") or "(no subject)"
        sender = _get_header(headers, "From") or "unknown@unknown.com"
        # "From" headers often look like "Name <email@domain.com>" — extract just the email.
        sender_email_match = re.search(r"<(.+?)>", sender)
        sender_clean = sender_email_match.group(1) if sender_email_match else sender

        body = _decode_body(payload)
        received_at = datetime.utcfromtimestamp(int(msg["internalDate"]) / 1000)

        emails.append(Email(
            id=msg["id"],
            user_id=user_id,
            source="gmail",
            sender=sender_clean,
            subject=subject,
            body=body[:5000],  # cap body length; plenty for keyword/date scoring
            received_at=received_at,
        ))
    return emails


def sync_gmail_emails(user_id: str = "local_user", allow_local_server: bool = False) -> dict:
    """Synchronizes recent Gmail emails into SQLite DB for a user.
    Returns a status dict: {"status": "success", "count": N, "email": email}
    or {"status": "auth_required", "message": "..."} / {"status": "error", "message": "..."}
    """
    db.init_db()
    is_auth, email_addr = is_gmail_authenticated()
    if not is_auth and not allow_local_server:
        return {"status": "auth_required", "message": "Gmail authentication required. Please login with Google."}

    try:
        prefs = db.get_preferences_model(user_id)
        service = get_gmail_service(allow_local_server=allow_local_server)
        emails = fetch_recent_emails(service, user_id=user_id)

        classified = [classify_email(e, prefs) for e in emails]
        for e in classified:
            db.upsert_email(e)
            db.create_reminders_for_email(e)

        return {
            "status": "success",
            "count": len(classified),
            "email": email_addr or "Connected Account",
            "emails": classified
        }
    except Exception as err:
        return {"status": "error", "message": str(err)}


def main():
    db.init_db()
    print("Connecting to Gmail (a browser window will open on first run)...")
    res = sync_gmail_emails(user_id="local_user", allow_local_server=True)
    if res.get("status") == "success":
        classified = res.get("emails", [])
        classified.sort(key=lambda e: e.importance_score, reverse=True)
        print(f"\n{'='*70}\nSAVED {len(classified)} EMAILS TO mail_expert.db\n{'='*70}")
        for e in classified:
            tier = e.importance.upper() if isinstance(e.importance, str) else e.importance.value.upper()
            print(f"[{tier}] (score={e.importance_score})  {e.category}  —  {e.subject[:60]}")

        print("\nDone. Run 'uvicorn api:app --reload' and open http://127.0.0.1:8000/ "
              "to view these in the web inbox.")
    else:
        print(f"[Error] Sync failed: {res.get('message')}")


if __name__ == "__main__":
    main()

