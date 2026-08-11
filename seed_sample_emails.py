"""
Seeds rich, realistic sample emails across categories and accounts into mail_expert.db.

Run:
    python seed_sample_emails.py
"""

from datetime import datetime, timedelta, timezone
import db
import importance_engine
from llm_processor import enrich_email_with_llm
from models import Email

NOW = datetime.now(timezone.utc).replace(tzinfo=None)

SAMPLE_EMAILS = [
    {
        "id": "sample_p1",
        "user_id": "local_user",
        "source": "gmail",
        "account_label": "College Gmail",
        "sender": "placements@university.edu",
        "subject": "URGENT: Final Call — Google Placement Registration Closing",
        "body": "Action required immediately: Google campus hiring registration closes tomorrow at 5 PM. Please submit your updated resume and confirm your interview slot within 24 hours. Mandatory attendance for shortlisted candidates.",
        "received_at": NOW - timedelta(hours=2),
    },
    {
        "id": "sample_p2",
        "user_id": "local_user",
        "source": "gmail",
        "account_label": "College Gmail",
        "sender": "tpo@university.edu",
        "subject": "Microsoft Internship Interview Confirmation & Slot Booking",
        "body": "Congratulations! You have been shortlisted for the Software Engineer intern interview scheduled on August 15, 2026. Confirm your slot by replying within 24 hours.",
        "received_at": NOW - timedelta(hours=5),
    },
    {
        "id": "sample_i1",
        "user_id": "local_user",
        "source": "outlook",
        "account_label": "Work Account",
        "sender": "recruiter@meta.com",
        "subject": "Meta Hackathon & Career Opportunity — Apply by Friday",
        "body": "We are hosting an exclusive engineering hackathon with full-time role offers. The last date to apply is August 18, 2026. Please fill out the registration form.",
        "received_at": NOW - timedelta(hours=10),
    },
    {
        "id": "sample_e1",
        "user_id": "local_user",
        "source": "gmail",
        "account_label": "Personal Gmail",
        "sender": "workshop@ai-summit.org",
        "subject": "Invitation: Global Generative AI Seminar & Hands-On Workshop",
        "body": "Join our upcoming live webinar session on building autonomous agentic tools. Registration ends tomorrow.",
        "received_at": NOW - timedelta(days=1),
    },
    {
        "id": "sample_c1",
        "user_id": "local_user",
        "source": "gmail",
        "account_label": "Personal Gmail",
        "sender": "newsletter@roboticsclub.org",
        "subject": "Robotics Club Weekly Digest & General Body Meeting",
        "body": "This is our weekly club newsletter, no action needed. Join our casual meet-up this weekend if interested. Unsubscribe anytime.",
        "received_at": NOW - timedelta(days=2),
    },
]


def seed():
    db.init_db()
    prefs = db.get_preferences_model("local_user")
    
    # Register accounts
    db.upsert_account("acc_college", "local_user", "College Gmail", "gmail", "student@university.edu")
    db.upsert_account("acc_work", "local_user", "Work Account", "outlook", "engineer@work.com")
    db.upsert_account("acc_personal", "local_user", "Personal Gmail", "gmail", "alex@gmail.com")

    count = 0
    for raw in SAMPLE_EMAILS:
        email = Email(
            id=raw["id"],
            user_id=raw["user_id"],
            source=raw["source"],
            sender=raw["sender"],
            subject=raw["subject"],
            body=raw["body"],
            received_at=raw["received_at"],
            account_label=raw["account_label"],
        )
        classified = importance_engine.classify_email(email, prefs, now=NOW)
        db.upsert_email(classified)
        db.create_reminders_for_email(classified)
        count += 1

    print(f"Successfully seeded {count} rich sample emails and reminders into mail_expert.db!")


if __name__ == "__main__":
    seed()
