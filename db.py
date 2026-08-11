"""
Lightweight SQLite persistence layer.

Why SQLite: zero setup (no server, no Docker), a single file (`mail_expert.db`)
that lives in your project folder, good enough for single-user MVP use.
Swappable later for Postgres by only touching this file — nothing else in
the app talks to the database directly.
"""

from __future__ import annotations
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from models import Email, Importance, Category, ExtractedDate

DB_PATH = "mail_expert.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    category TEXT NOT NULL,
    importance TEXT,
    importance_score REAL DEFAULT 0.0,
    score_breakdown TEXT DEFAULT '{}',
    extracted_dates TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    summary TEXT,
    action_items TEXT DEFAULT '[]',
    user_override TEXT,
    is_read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    email_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    due_at TEXT NOT NULL,
    notify_offsets_minutes TEXT DEFAULT '[1440, 60]',
    channels TEXT DEFAULT '["desktop"]',
    notified_offsets TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS override_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    email_id TEXT NOT NULL,
    original_importance TEXT,
    override_importance TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    email_address TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    last_synced_at TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Handle lightweight schema migration for existing databases
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(emails)").fetchall()]
        if "summary" not in cols:
            conn.execute("ALTER TABLE emails ADD COLUMN summary TEXT")
        if "action_items" not in cols:
            conn.execute("ALTER TABLE emails ADD COLUMN action_items TEXT DEFAULT '[]'")
        if "account_label" not in cols:
            conn.execute("ALTER TABLE emails ADD COLUMN account_label TEXT DEFAULT 'Primary Account'")


def upsert_email(email: Email):
    """Insert a newly classified email, or update it if we've seen this id
    before (e.g. re-running the connector on an overlapping set of emails).
    Deliberately does NOT overwrite is_read or user_override on update, so
    re-classifying doesn't wipe out things the user already did."""
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM emails WHERE id = ?", (email.id,)).fetchone()
        account_lbl = getattr(email, "account_label", "Primary Account") or "Primary Account"
        if existing:
            conn.execute("""
                UPDATE emails SET
                    category = ?, importance = ?, importance_score = ?,
                    score_breakdown = ?, extracted_dates = ?,
                    summary = ?, action_items = ?, account_label = ?
                WHERE id = ?
            """, (
                email.category.value if hasattr(email.category, "value") else email.category,
                email.importance.value if hasattr(email.importance, "value") else email.importance,
                email.importance_score,
                json.dumps(email.score_breakdown),
                json.dumps([d.model_dump(mode="json") for d in email.extracted_dates]),
                email.summary,
                json.dumps(email.action_items),
                account_lbl,
                email.id,
            ))
        else:
            conn.execute("""
                INSERT INTO emails
                (id, user_id, source, sender, subject, body, received_at,
                 category, importance, importance_score, score_breakdown,
                 extracted_dates, tags, summary, action_items, user_override, is_read, account_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                email.id, email.user_id, email.source, email.sender,
                email.subject, email.body, email.received_at.isoformat(),
                email.category.value if hasattr(email.category, "value") else email.category,
                email.importance.value if hasattr(email.importance, "value") else email.importance,
                email.importance_score,
                json.dumps(email.score_breakdown),
                json.dumps([d.model_dump(mode="json") for d in email.extracted_dates]),
                json.dumps(email.tags),
                email.summary,
                json.dumps(email.action_items),
                email.user_override,
                int(email.is_read),
                account_lbl,
            ))


def get_all_emails(user_id: str, importance_filter: Optional[str] = None, account_filter: Optional[str] = None) -> List[dict]:
    """Returns raw dicts (already JSON-friendly) sorted by importance_score
    descending — highest priority first. Used directly by the web UI."""
    query = "SELECT * FROM emails WHERE user_id = ?"
    params: list = [user_id]
    if importance_filter:
        query += " AND importance = ?"
        params.append(importance_filter)
    if account_filter:
        query += " AND account_label = ?"
        params.append(account_filter)
    query += " ORDER BY importance_score DESC, received_at DESC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["score_breakdown"] = json.loads(d["score_breakdown"] or "{}")
        d["extracted_dates"] = json.loads(d["extracted_dates"] or "[]")
        d["tags"] = json.loads(d["tags"] or "[]")
        d["action_items"] = json.loads(d.get("action_items") or "[]")
        d["is_read"] = bool(d["is_read"])
        d["account_label"] = d.get("account_label") or "Primary Account"
        results.append(d)
    return results


def mark_read(email_id: str, is_read: bool = True):
    with get_conn() as conn:
        conn.execute("UPDATE emails SET is_read = ? WHERE id = ?", (int(is_read), email_id))


def set_user_override(email_id: str, importance: str):
    """Lets the user manually correct a tier. Logs overrides into override_logs for auto-tuning."""
    with get_conn() as conn:
        email_row = conn.execute("SELECT user_id, importance, category FROM emails WHERE id = ?", (email_id,)).fetchone()
        conn.execute(
            "UPDATE emails SET user_override = ?, importance = ? WHERE id = ?",
            (importance, importance, email_id),
        )
        if email_row:
            user_id = email_row["user_id"]
            orig_imp = email_row["importance"]
            cat = email_row["category"]
            conn.execute(
                """INSERT INTO override_logs
                   (user_id, email_id, original_importance, override_importance, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, email_id, orig_imp, importance, cat, datetime.now(timezone.utc).isoformat())
            )


def get_override_logs(user_id: str) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM override_logs WHERE user_id = ?", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_email_by_id(email_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["score_breakdown"] = json.loads(d["score_breakdown"] or "{}")
        d["extracted_dates"] = json.loads(d["extracted_dates"] or "[]")
        d["tags"] = json.loads(d["tags"] or "[]")
        d["action_items"] = json.loads(d.get("action_items") or "[]")
        d["is_read"] = bool(d["is_read"])
        return d


def update_email_summary(email_id: str, summary: str, action_items: List[str]):
    with get_conn() as conn:
        conn.execute(
            "UPDATE emails SET summary = ?, action_items = ? WHERE id = ?",
            (summary, json.dumps(action_items), email_id),
        )


def delete_email(email_id: str):
    """Deletes an email and its associated reminders from the database by its ID."""
    with get_conn() as conn:
        conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
        conn.execute("DELETE FROM reminders WHERE email_id = ?", (email_id,))


def delete_all(user_id: str):
    """Useful for resetting during testing."""
    with get_conn() as conn:
        conn.execute("DELETE FROM emails WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM override_logs WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM preferences WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def create_reminders_for_email(email: Email):
    """
    Turns an email's credible, future-dated extracted_dates into Reminder
    rows. Safe to call every time an email is (re-)classified — skips dates
    that already have a reminder (id is deterministic: f"{email.id}-{index}").
    High-importance emails get an extra early heads-up (1 week out).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tier = email.importance.value if hasattr(email.importance, "value") else email.importance
    offsets = [7 * 24 * 60, 24 * 60, 60] if tier == "high" else [24 * 60, 60]

    with get_conn() as conn:
        for i, d in enumerate(email.extracted_dates):
            if d.confidence < 0.6:
                continue
            if d.datetime_utc <= now:
                continue  # don't create reminders for dates already passed

            reminder_id = f"{email.id}-rem-{i}"
            existing = conn.execute("SELECT id FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
            if existing:
                continue

            conn.execute("""
                INSERT INTO reminders (id, email_id, user_id, title, due_at, notify_offsets_minutes, channels)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                reminder_id, email.id, email.user_id,
                f"{d.label}: {email.subject}",
                d.datetime_utc.isoformat(),
                json.dumps(offsets),
                json.dumps(["desktop"]),
            ))


def get_due_notifications(now: Optional[datetime] = None) -> List[dict]:
    """
    Returns (reminder_dict, offset_minutes) pairs that are due to fire RIGHT
    NOW and haven't fired for that specific offset yet. The scheduler calls
    this every polling cycle.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    due = []
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reminders WHERE status = 'pending'").fetchall()
        for row in rows:
            r = dict(row)
            due_at = datetime.fromisoformat(r["due_at"])
            offsets = json.loads(r["notify_offsets_minutes"])
            notified = json.loads(r["notified_offsets"])

            for offset_minutes in offsets:
                if offset_minutes in notified:
                    continue
                trigger_time = due_at - timedelta(minutes=offset_minutes)
                if now >= trigger_time:
                    due.append((r, offset_minutes))
    return due


def mark_offset_notified(reminder_id: str, offset_minutes: int):
    with get_conn() as conn:
        row = conn.execute("SELECT notified_offsets FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            return
        notified = json.loads(row["notified_offsets"])
        if offset_minutes not in notified:
            notified.append(offset_minutes)
        conn.execute(
            "UPDATE reminders SET notified_offsets = ? WHERE id = ?",
            (json.dumps(notified), reminder_id),
        )


def dismiss_reminder(reminder_id: str):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET status = 'dismissed' WHERE id = ?", (reminder_id,))


def get_upcoming_reminders(user_id: str) -> List[dict]:
    """For the agenda view — all pending reminders, soonest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND status = 'pending' ORDER BY due_at ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

DEFAULT_PREFS_DICT = {
    "timezone": "UTC",
    "sender_rules": [],  # [{"sender": "...", "action": "always_high|always_low|mute"}]
    "category_weights": {
        "placement": 1.0, "industry": 0.8, "club": 0.4, "event": 0.6, "uncategorized": 0.3,
    },
    "high_threshold": 0.7,
    "medium_threshold": 0.4,
}


def get_preferences_dict(user_id: str) -> dict:
    """Returns the raw preferences dict for a user, creating defaults on first access."""
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return json.loads(row["data"])
        conn.execute(
            "INSERT INTO preferences (user_id, data) VALUES (?, ?)",
            (user_id, json.dumps(DEFAULT_PREFS_DICT)),
        )
        return dict(DEFAULT_PREFS_DICT)


def save_preferences_dict(user_id: str, prefs_dict: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO preferences (user_id, data) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET data = excluded.data
        """, (user_id, json.dumps(prefs_dict)))


def get_user_accounts(user_id: str) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts WHERE user_id = ? ORDER BY account_name ASC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def upsert_account(account_id: str, user_id: str, account_name: str, provider: str, email_address: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO accounts (id, user_id, account_name, provider, email_address, is_active, last_synced_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_name = excluded.account_name,
                provider = excluded.provider,
                email_address = excluded.email_address,
                last_synced_at = excluded.last_synced_at
        """, (account_id, user_id, account_name, provider, email_address, datetime.now(timezone.utc).isoformat()))


def delete_account(account_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def get_preferences_model(user_id: str) -> "Preferences":
    """Returns a Preferences pydantic object (what the engine expects) built
    from whatever is saved in the DB — this is the bridge between the
    persisted preferences and classify_email()."""
    from models import Preferences  # local import to avoid a circular import at module load time
    return Preferences(user_id=user_id, **get_preferences_dict(user_id))
