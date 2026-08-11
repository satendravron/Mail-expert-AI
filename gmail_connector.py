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


def get_gmail_service():
    """Handles the OAuth dance. First run = browser popup. Later runs = silent."""
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
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)  # opens the browser login window

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


def main():
    db.init_db()
    prefs = db.get_preferences_model("local_user")  # loads saved prefs, or creates defaults on first run

    print("Connecting to Gmail (a browser window will open on first run)...")
    service = get_gmail_service()

    print(f"Fetching your {MAX_EMAILS_TO_FETCH} most recent inbox emails...")
    emails = fetch_recent_emails(service, user_id="local_user")

    classified = [classify_email(e, prefs) for e in emails]
    for e in classified:
        db.upsert_email(e)
        db.create_reminders_for_email(e)

    classified.sort(key=lambda e: e.importance_score, reverse=True)

    print(f"\n{'='*70}\nSAVED {len(classified)} EMAILS TO mail_expert.db\n{'='*70}")
    for e in classified:
        tier = e.importance.upper() if isinstance(e.importance, str) else e.importance.value.upper()
        print(f"[{tier}] (score={e.importance_score})  {e.category}  —  {e.subject[:60]}")

    print("\nDone. Run 'uvicorn api:app --reload' and open http://127.0.0.1:8000/ "
          "to view these in the web inbox.")


if __name__ == "__main__":
    main()
