"""
Quick test: creates a FAKE email with a deadline 2 minutes from now, saves it
(with a reminder), and tells you exactly when to expect a popup.

Run this, then make sure `reminder_scheduler.py` is running in another
terminal — within ~2 minutes (plus up to 60s for the next poll cycle) you
should see a real desktop notification appear.

Run:
    python test_reminder_popup.py
"""

import sys
from datetime import datetime, timedelta, timezone
from models import Email, Preferences
from importance_engine import classify_email
import db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

db.init_db()
prefs = Preferences(user_id="local_user")
now = datetime.now(timezone.utc).replace(tzinfo=None)

fake_due_time = now + timedelta(minutes=2)

# We build the Email directly with an extracted_dates entry set manually
# instead of relying on text-parsing, so the test is 100% deterministic.
from models import ExtractedDate

e = Email(
    id="test-popup-1",
    user_id="local_user",
    source="test",
    sender="test@example.com",
    subject="TEST: This is a fake deadline to check notifications work",
    body="This is a test email only.",
    received_at=now,
)
classify_email(e, prefs, now=now)  # still runs normal scoring/category logic

# Override with our controlled test date + a very short offset so it fires almost immediately.
e.extracted_dates = [
    ExtractedDate(
        label="TEST Deadline",
        datetime_utc=fake_due_time,
        confidence=0.95,
        raw_text="test-generated",
    )
]

db.upsert_email(e)

# Manually insert a reminder with a 1-minute offset so it fires almost right away,
# instead of using the normal 24h/1h offsets which would be too far out for a quick test.
with db.get_conn() as conn:
    conn.execute("DELETE FROM reminders WHERE email_id = ?", (e.id,))
    conn.execute("""
        INSERT INTO reminders (id, email_id, user_id, title, due_at, notify_offsets_minutes, channels)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "test-popup-1-rem-0", e.id, e.user_id,
        "TEST Deadline: fake reminder to confirm notifications work",
        fake_due_time.isoformat(),
        "[1]",  # fire when we're 1 minute away from "due"
        '["desktop"]',
    ))

print(f"Fake deadline set for: {fake_due_time.strftime('%H:%M:%S')} (now is {now.strftime('%H:%M:%S')})")
print("This reminder will fire when we're within 1 minute of that time.")
print("Make sure 'python reminder_scheduler.py' is running in another terminal.")
print("Within ~1-2 minutes (it polls every 60s) you should see a desktop popup appear.")
