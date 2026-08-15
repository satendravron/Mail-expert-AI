"""
Reminder Scheduler for Mail Expert AI.

Run this in its OWN terminal, alongside `uvicorn api:app --reload`. It:
  1. Checks the database every POLL_INTERVAL_SECONDS for reminders that are
     due to fire (based on each reminder's notify_offsets_minutes).
  2. Pops a native desktop notification (Windows toast / macOS notification
     center / Linux notify-send, via `plyer`) for each one.
  3. Marks that specific offset as "notified" so you don't get the same
     popup twice.

Keep this running in the background (e.g. a second VS Code terminal tab)
while you work — it's what turns "deadlines sitting in a list" into
"deadlines that actually interrupt you."

Run:
    python reminder_scheduler.py
Stop:
    Ctrl+C in that terminal
"""

from __future__ import annotations
import sys
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from plyer import notification

import db

POLL_INTERVAL_SECONDS = 60  # check every minute; cheap enough to run constantly


def fire_desktop_notification(title: str, message: str):
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Mail Expert AI",
            timeout=15,
        )
    except Exception as e:
        print(f"[warn] Could not show desktop notification: {e}")



def offset_label(offset_minutes: int) -> str:
    if offset_minutes >= 24 * 60:
        days = offset_minutes // (24 * 60)
        return f"{days} day{'s' if days != 1 else ''} left"
    if offset_minutes >= 60:
        hours = offset_minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''} left"
    return f"{offset_minutes} min left"


def run_once():
    """One polling cycle — checks for due reminders and fires notifications."""
    due = db.get_due_notifications(now=datetime.now(timezone.utc).replace(tzinfo=None))
    for reminder, offset_minutes in due:
        title = f"⏰ {offset_label(offset_minutes)}"
        message = reminder["title"]
        fire_desktop_notification(title, message)
        db.mark_offset_notified(reminder["id"], offset_minutes)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Notified: {message} ({offset_label(offset_minutes)})")


def main():
    db.init_db()
    print(f"Reminder scheduler running. Checking every {POLL_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")
    try:
        while True:
            run_once()
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
