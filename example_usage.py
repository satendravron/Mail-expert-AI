"""
Standalone demo — no server needed. Run: python example_usage.py
Shows the engine classifying 4 realistic emails using a sample Preferences
object, including sender-rule overrides and score explainability.
"""

import sys
from datetime import datetime, timedelta
from models import Email, Preferences, SenderRule, SenderAction
from importance_engine import classify_email

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NOW = datetime(2026, 7, 9, 9, 0, 0)  # fixed "now" so the demo is reproducible

prefs = Preferences(
    user_id="u1",
    timezone="Asia/Kolkata",
    sender_rules=[
        SenderRule(sender="@college-placements.edu", action=SenderAction.ALWAYS_HIGH),
        SenderRule(sender="spam@randomclub.com", action=SenderAction.MUTE),
    ],
    category_weights={
        "placement": 1.0,
        "industry": 0.8,
        "club": 0.35,
        "event": 0.55,
        "uncategorized": 0.3,
    },
)

sample_emails = [
    Email(
        id="e1", user_id="u1", source="gmail",
        sender="tpo@college-placements.edu",
        subject="URGENT: Infosys Shortlist — Last Date to Confirm Tomorrow",
        body="Congratulations, you have been shortlisted. Please confirm your "
             "slot within 24 hours. Interview scheduled next week. Action required.",
        received_at=NOW,
    ),
    Email(
        id="e2", user_id="u1", source="gmail",
        sender="events@codingclub.org",
        subject="Weekly Newsletter — Club Updates",
        body="This is our regular newsletter, no action needed. Unsubscribe anytime.",
        received_at=NOW,
    ),
    Email(
        id="e3", user_id="u1", source="gmail",
        sender="hr@somecorp.com",
        subject="Internship Deadline Extended",
        body="The last date to apply for our internship program is in 3 days. "
             "Please submit your application before the deadline.",
        received_at=NOW,
    ),
    Email(
        id="e4", user_id="u1", source="gmail",
        sender="spam@randomclub.com",
        subject="Don't miss this!!",
        body="Buy now, limited offer.",
        received_at=NOW,
    ),
]

for email in sample_emails:
    classified = classify_email(email, prefs, now=NOW)
    print(f"\n--- {classified.id} ---")
    print(f"Sender     : {classified.sender}")
    print(f"Subject    : {classified.subject}")
    print(f"Category   : {classified.category}")
    print(f"Importance : {classified.importance}  (score={classified.importance_score})")
    print(f"Breakdown  : {classified.score_breakdown}")
    if classified.extracted_dates:
        for d in classified.extracted_dates:
            print(f"  → {d.label}: {d.datetime_utc} (confidence={d.confidence}) [\"{d.raw_text}\"]")
