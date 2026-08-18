"""
Automated Test Suite for Mail Expert AI.

Runs full unit and integration tests across:
  - Date Extractor (regex, relative dates, confidence)
  - Importance Engine (keyword scoring, category detection, deadline boost, sender overrides)
  - Database Persistence (SQLite CRUD, preferences, reminders)
  - FastAPI Web Service (Endpoints, classification, overrides)

Run:
    python test_suite.py
    # or
    python -m unittest test_suite.py
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from models import Email, Preferences, SenderRule, SenderAction, Importance, Category, ExtractedDate
from date_extractor import extract_dates
from importance_engine import classify_email, batch_classify
import db

from fastapi.testclient import TestClient
from api import app

FIXED_NOW = datetime(2026, 7, 10, 10, 0, 0)


class TestDateExtractor(unittest.TestCase):
    def test_anchored_deadline_extraction(self):
        subject = "URGENT: Application Notice"
        body = "The last date to apply for this position is July 15, 2026."
        dates = extract_dates(subject, body, now=FIXED_NOW)
        self.assertGreaterEqual(len(dates), 1)
        self.assertEqual(dates[0].label, "Application Deadline")
        self.assertGreaterEqual(dates[0].confidence, 0.8)
        self.assertEqual(dates[0].datetime_utc.year, 2026)
        self.assertEqual(dates[0].datetime_utc.month, 7)
        self.assertEqual(dates[0].datetime_utc.day, 15)

    def test_relative_date_extraction(self):
        subject = "Confirmation Required"
        body = "Please reply within 24 hours to confirm your interview."
        dates = extract_dates(subject, body, now=FIXED_NOW)
        self.assertGreaterEqual(len(dates), 1)
        expected_time = FIXED_NOW + timedelta(hours=24)
        self.assertEqual(dates[0].datetime_utc, expected_time)

    def test_in_x_days_extraction(self):
        subject = "Internship Opportunity"
        body = "Submit resume in 3 days."
        dates = extract_dates(subject, body, now=FIXED_NOW)
        self.assertGreaterEqual(len(dates), 1)
        expected_time = FIXED_NOW + timedelta(days=3)
        self.assertEqual(dates[0].datetime_utc, expected_time)


class TestImportanceEngine(unittest.TestCase):
    def setUp(self):
        self.prefs = Preferences(
            user_id="test_user",
            sender_rules=[
                SenderRule(sender="@vip-college.edu", action=SenderAction.ALWAYS_HIGH),
                SenderRule(sender="spammer@club.com", action=SenderAction.MUTE),
            ],
            category_weights={
                "placement": 1.0,
                "industry": 0.8,
                "club": 0.3,
                "event": 0.5,
                "uncategorized": 0.2,
            },
            high_threshold=0.7,
            medium_threshold=0.4,
        )

    def test_always_high_sender_rule(self):
        email = Email(
            id="e_high",
            user_id="test_user",
            source="gmail",
            sender="director@vip-college.edu",
            subject="Regular update",
            body="Just a note.",
            received_at=FIXED_NOW,
        )
        res = classify_email(email, self.prefs, now=FIXED_NOW)
        self.assertEqual(res.importance, Importance.HIGH)
        self.assertEqual(res.importance_score, 1.0)
        self.assertEqual(res.score_breakdown.get("sender_rule"), "always_high")

    def test_muted_sender_rule(self):
        email = Email(
            id="e_muted",
            user_id="test_user",
            source="gmail",
            sender="spammer@club.com",
            subject="URGENT DEADLINE!!",
            body="Buy tickets now!",
            received_at=FIXED_NOW,
        )
        res = classify_email(email, self.prefs, now=FIXED_NOW)
        self.assertEqual(res.importance, Importance.LOW)
        self.assertEqual(res.importance_score, 0.0)
        self.assertEqual(res.score_breakdown.get("sender_rule"), "muted")

    def test_urgency_and_category_scoring(self):
        email = Email(
            id="e_urg",
            user_id="test_user",
            source="gmail",
            sender="hr@techcorp.com",
            subject="URGENT: Placement Interview Shortlist",
            body="Action required: Confirm your interview slot within 24 hours. Mandatory attendance.",
            received_at=FIXED_NOW,
        )
        res = classify_email(email, self.prefs, now=FIXED_NOW)
        self.assertEqual(res.category, Category.PLACEMENT)
        self.assertEqual(res.importance, Importance.HIGH)
        self.assertGreaterEqual(res.importance_score, 0.7)

    def test_low_signal_newsletter_penalty(self):
        email = Email(
            id="e_news",
            user_id="test_user",
            source="gmail",
            sender="news@club.org",
            subject="Weekly Club Newsletter",
            body="This is our weekly newsletter, no action needed. Unsubscribe here.",
            received_at=FIXED_NOW,
        )
        res = classify_email(email, self.prefs, now=FIXED_NOW)
        self.assertEqual(res.importance, Importance.LOW)
        self.assertLess(res.importance_score, 0.4)


class TestDatabaseLayer(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.delete_all("test_db_user")

    def tearDown(self):
        db.delete_all("test_db_user")

    def test_upsert_and_retrieve_email(self):
        email = Email(
            id="db_e1",
            user_id="test_db_user",
            source="gmail",
            sender="test@domain.com",
            subject="Test Subject",
            body="Test body content",
            received_at=FIXED_NOW,
            category=Category.PLACEMENT,
            importance=Importance.HIGH,
            importance_score=0.85,
        )
        db.upsert_email(email)
        fetched = db.get_all_emails("test_db_user")
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0]["id"], "db_e1")
        self.assertEqual(fetched[0]["importance"], "high")
        self.assertEqual(fetched[0]["importance_score"], 0.85)

    def test_user_override(self):
        email = Email(
            id="db_e2",
            user_id="test_db_user",
            source="gmail",
            sender="test@domain.com",
            subject="Test Subject 2",
            body="Test body content 2",
            received_at=FIXED_NOW,
            importance=Importance.LOW,
            importance_score=0.2,
        )
        db.upsert_email(email)
        db.set_user_override("db_e2", "high")
        fetched = db.get_all_emails("test_db_user")
        self.assertEqual(fetched[0]["importance"], "high")
        self.assertEqual(fetched[0]["user_override"], "high")

    def test_reminders_creation_and_polling(self):
        now_time = datetime.now(timezone.utc).replace(tzinfo=None)
        future_date = now_time + timedelta(days=2)
        email = Email(
            id="db_e3",
            user_id="test_db_user",
            source="gmail",
            sender="test@domain.com",
            subject="Deadline Test",
            body="Deadline soon",
            received_at=now_time,
            importance=Importance.HIGH,
            extracted_dates=[
                ExtractedDate(
                    label="Deadline",
                    datetime_utc=future_date,
                    confidence=0.9,
                    raw_text="due soon",
                )
            ],
        )
        db.create_reminders_for_email(email)
        upcoming = db.get_upcoming_reminders("test_db_user")
        self.assertGreaterEqual(len(upcoming), 1)
        rem_id = upcoming[0]["id"]
        self.assertEqual(upcoming[0]["email_id"], "db_e3")

        # Test 1: Dismissing reminder updates status to 'dismissed'
        db.dismiss_reminder(rem_id)
        pending_after = db.get_upcoming_reminders("test_db_user")
        self.assertEqual(len([r for r in pending_after if r["id"] == rem_id]), 0)

        # Test 2: Re-running auto-extracted reminders does NOT resurrect dismissed alarm
        db.create_reminders_for_email(email)
        pending_retest = db.get_upcoming_reminders("test_db_user")
        self.assertEqual(len([r for r in pending_retest if r["id"] == rem_id]), 0)

        # Test 3: Explicitly setting an alarm for this email re-activates reminder status to pending
        reactivated = db.create_custom_reminder(
            user_id="test_db_user",
            title="Follow up: Explicit alarm set",
            due_at=(now_time + timedelta(days=3)).isoformat(),
            email_id="db_e3"
        )
        self.assertEqual(reactivated["status"], "pending")
        pending_final = db.get_upcoming_reminders("test_db_user")
        self.assertGreaterEqual(len([r for r in pending_final if r["email_id"] == "db_e3"]), 1)


class TestFastAPIEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        db.init_db()

    def test_health_check(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")

    def test_classify_endpoint(self):
        payload = {
            "email": {
                "id": "api_e1",
                "user_id": "local_user",
                "source": "api_test",
                "sender": "recruit@bigtech.com",
                "subject": "URGENT: Final Call for Placement Registration",
                "body": "Action required: Complete registration before deadline tomorrow.",
                "received_at": FIXED_NOW.isoformat(),
            },
            "preferences": {
                "user_id": "local_user",
                "timezone": "UTC",
                "sender_rules": [],
                "category_weights": {"placement": 1.0},
                "high_threshold": 0.7,
                "medium_threshold": 0.4,
            },
        }
        res = self.client.post("/emails/classify", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["category"], "placement")
        self.assertEqual(data["importance"], "high")

    def test_preferences_json_api(self):
        res = self.client.get("/api/preferences?user_id=test_api_user")
        self.assertEqual(res.status_code, 200)
        prefs = res.json()
        self.assertIn("category_weights", prefs)

        prefs["high_threshold"] = 0.75
        save_res = self.client.post("/api/preferences?user_id=test_api_user", json=prefs)
        self.assertEqual(save_res.status_code, 200)

        updated_res = self.client.get("/api/preferences?user_id=test_api_user")
        self.assertEqual(updated_res.json()["high_threshold"], 0.75)


class TestLLMProcessor(unittest.TestCase):
    def test_extractive_summary_and_actions(self):
        import llm_processor
        subject = "URGENT: Placement Registration"
        body = "Please submit your resume by tomorrow. Fill out the Google form immediately. Thank you."
        res = llm_processor.generate_email_summary_and_actions(subject, body)
        self.assertIn("summary", res)
        self.assertIn("action_items", res)
        self.assertTrue(len(res["action_items"]) > 0)

    def test_enrich_email(self):
        import llm_processor
        email = Email(
            id="llm_e1",
            user_id="test_user",
            source="gmail",
            sender="admin@test.com",
            subject="Interview Confirmation",
            body="Confirm your interview slot within 24 hours.",
            received_at=FIXED_NOW,
        )
        enriched = llm_processor.enrich_email_with_llm(email)
        self.assertIsNotNone(enriched.summary)
        self.assertGreater(len(enriched.action_items), 0)


class TestCalendarProcessor(unittest.TestCase):
    def test_ics_generation(self):
        import calendar_processor
        dt = datetime(2026, 8, 15, 14, 0, 0, tzinfo=timezone.utc)
        ics = calendar_processor.generate_ics_content("Placement Deadline", dt, description="Submit resume")
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("SUMMARY:Placement Deadline", ics)
        self.assertIn("20260815T140000Z", ics)
        self.assertIn("END:VCALENDAR", ics)

    def test_gcal_link_building(self):
        import calendar_processor
        dt = datetime(2026, 8, 15, 14, 0, 0, tzinfo=timezone.utc)
        url = calendar_processor.generate_gcal_web_link("Interview Date", dt, description="Confirm slot")
        self.assertIn("https://calendar.google.com/calendar/render", url)
        self.assertIn("action=TEMPLATE", url)
        self.assertIn("text=Interview+Date", url)


class TestLearningProcessor(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.delete_all("test_learn_user")

    def test_override_logging_and_autotune(self):
        import learning_processor

        email = Email(
            id="learn_e1",
            user_id="test_learn_user",
            source="gmail",
            sender="club@school.edu",
            subject="Club Event Announcement",
            body="Annual club fest session tomorrow",
            category=Category.CLUB,
            importance=Importance.LOW,
            received_at=FIXED_NOW,
        )
        db.upsert_email(email)
        db.set_user_override("learn_e1", "high")

        logs = db.get_override_logs("test_learn_user")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["override_importance"], "high")

        res = learning_processor.auto_tune_user_preferences("test_learn_user")
        self.assertEqual(res["status"], "success")
        self.assertIn("club", res["category_weights"])
        self.assertGreater(res["category_weights"]["club"], 0.4)


class TestDraftProcessor(unittest.TestCase):
    def test_draft_generation_intents(self):
        import draft_processor
        for intent in ["confirm", "extension", "accept", "decline", "clarification"]:
            draft = draft_processor.generate_reply_draft(
                subject="Placement Interview Invitation",
                body="Please confirm your availability for tomorrow.",
                sender="Recruiter <hr@tech.com>",
                intent=intent
            )
            self.assertIn("reply_subject", draft)
            self.assertIn("reply_body", draft)
            self.assertTrue(len(draft["reply_body"]) > 20)


class TestMultiInboxConnector(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.delete_all("test_multi_user")

    def test_account_registration_and_ingestion(self):
        from multi_inbox_connector import MultiInboxConnector
        connector = MultiInboxConnector("test_multi_user")
        reg = connector.register_account("College Gmail", "gmail", "student@college.edu")
        self.assertEqual(reg["status"], "success")

        accs = connector.get_accounts()
        self.assertGreaterEqual(len(accs), 1)

        raw_emails = [{
            "id": "multi_e1",
            "source": "gmail",
            "sender": "placements@college.edu",
            "subject": "URGENT: Interview Slot",
            "body": "Confirm slot within 24 hours.",
            "received_at": FIXED_NOW.isoformat()
        }]
        ingested = connector.ingest_emails_for_account("College Gmail", raw_emails)
        self.assertEqual(len(ingested), 1)
        self.assertEqual(ingested[0].account_label, "College Gmail")

        db_emails = db.get_all_emails("test_multi_user", account_filter="College Gmail")
        self.assertEqual(len(db_emails), 1)
        self.assertEqual(db_emails[0]["account_label"], "College Gmail")

        # Test account deletion removes account and associated emails/reminders
        acc_id = reg["account_id"]
        connector.delete_account(acc_id)
        accs_after = db.get_user_accounts("test_multi_user")
        self.assertFalse(any(a["id"] == acc_id for a in accs_after))
        db_emails_after = db.get_all_emails("test_multi_user", account_filter="College Gmail")
        self.assertEqual(len(db_emails_after), 0)


class TestGmailLogoutAndCleanup(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.delete_all("test_logout_user")

    def test_logout_gmail_clears_emails_and_reminders(self):
        import gmail_connector
        # Create a Gmail email and a non-Gmail email
        gmail_email = Email(
            id="gmail_test_1",
            user_id="test_logout_user",
            source="gmail",
            sender="boss@company.com",
            subject="Project update deadline July 20",
            body="Please update by July 20, 2026",
            received_at=FIXED_NOW,
            extracted_dates=[ExtractedDate(label="Deadline", datetime_utc=datetime(2026, 7, 20, 10, 0, 0), confidence=0.9, raw_text="July 20")]
        )
        other_email = Email(
            id="manual_test_1",
            user_id="test_logout_user",
            source="manual",
            sender="friend@hobby.com",
            subject="Weekend plans",
            body="Let us meet on Saturday",
            received_at=FIXED_NOW
        )
        db.upsert_email(gmail_email)
        db.create_reminders_for_email(gmail_email)
        db.upsert_email(other_email)

        # Confirm inserted
        self.assertEqual(len(db.get_all_emails("test_logout_user")), 2)

        # Perform logout for test_logout_user
        success = gmail_connector.logout_gmail(user_id="test_logout_user", clear_emails=True)
        self.assertTrue(success)

        # Confirm all emails and reminders are cleared after logout
        remaining = db.get_all_emails("test_logout_user")
        self.assertEqual(len(remaining), 0)

    def test_auth_logout_api_endpoint(self):
        client = TestClient(app)
        res = client.post("/auth/logout?user_id=test_logout_user")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["connected"])


class TestDirectOutboundEmailSending(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.delete_all("test_send_user")
        self.email = Email(
            id="send_test_1",
            user_id="test_send_user",
            source="mock",
            sender="interviewer@company.com",
            subject="Interview Invitation",
            body="Please confirm your availability for tomorrow.",
            received_at=FIXED_NOW
        )
        db.upsert_email(self.email)

    def test_send_reply_api_endpoint(self):
        client = TestClient(app)
        payload = {
            "email_id": "send_test_1",
            "recipient": "interviewer@company.com",
            "subject": "Re: Interview Invitation",
            "body": "I confirm my availability. Thank you!",
            "intent": "confirm"
        }
        res = client.post("/emails/send_test_1/send-reply", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["email_id"], "send_test_1")

        # Verify DB status updated
        e = db.get_email_by_id("send_test_1")
        self.assertTrue(e["is_replied"])
        self.assertIsNotNone(e["reply_sent_at"])


class TestAnalyticsEndpoint(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_analytics_api(self):
        client = TestClient(app)
        res = client.get("/api/analytics?user_id=local_user")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("total_count", data)
        self.assertIn("priority_distribution", data)
        self.assertIn("category_distribution", data)
        self.assertIn("top_senders", data)
        self.assertIn("upcoming_deadlines", data)


class TestWebSocketAndWebhooks(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_incoming_webhook_endpoint(self):
        client = TestClient(app)
        payload = {
            "id": "wh_test_1",
            "sender": "webhook_sender@company.com",
            "subject": "URGENT: Webhook Project Deadline",
            "body": "Please complete by tomorrow evening.",
            "source": "webhook"
        }
        res = client.post("/api/webhooks/incoming", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["email_id"], "wh_test_1")

        e = db.get_email_by_id("wh_test_1")
        self.assertIsNotNone(e)
        self.assertEqual(e["subject"], "URGENT: Webhook Project Deadline")

    def test_websocket_connection(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/inbox") as websocket:
            websocket.send_text("ping")


class TestMultiUserAuth(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_user_registration_and_login(self):
        import uuid
        test_email = f"user_{uuid.uuid4().hex[:6]}@example.com"
        client = TestClient(app)
        reg_payload = {
            "email": test_email,
            "password": "SecurePassword123!",
            "full_name": "Test User"
        }
        res = client.post("/api/auth/register", json=reg_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["email"], test_email)
        self.assertEqual(data["full_name"], "Test User")

        # Test duplicate registration fails
        res_dup = client.post("/api/auth/register", json=reg_payload)
        self.assertEqual(res_dup.status_code, 400)

        # Test Login
        login_payload = {
            "email": test_email,
            "password": "SecurePassword123!"
        }
        res_login = client.post("/api/auth/login", json=login_payload)
        self.assertEqual(res_login.status_code, 200)
        login_data = res_login.json()
        self.assertIn("access_token", login_data)

        # Test protected profile endpoint /api/auth/me
        token = login_data["access_token"]
        res_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res_me.status_code, 200)
        me_data = res_me.json()
        self.assertTrue(me_data["is_authenticated"])
        self.assertEqual(me_data["email"], test_email)


class TestExportImportBackupEngine(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_export_json_and_csv(self):
        client = TestClient(app)

        # Test JSON Export
        res_json = client.get("/api/backup/export?format=json")
        self.assertEqual(res_json.status_code, 200)
        self.assertIn("application/json", res_json.headers.get("content-type", ""))
        backup_dict = res_json.json()
        self.assertEqual(backup_dict["app"], "Mail Expert AI")
        self.assertIn("emails", backup_dict)
        self.assertIn("reminders", backup_dict)

        # Test CSV Export
        res_csv = client.get("/api/backup/export?format=csv")
        self.assertEqual(res_csv.status_code, 200)
        self.assertIn("text/csv", res_csv.headers.get("content-type", ""))
        csv_text = res_csv.text
        self.assertIn("id,sender,subject", csv_text)

    def test_import_backup_restoration(self):
        client = TestClient(app)
        sample_backup = {
            "version": "1.0",
            "app": "Mail Expert AI",
            "user_id": "test_backup_user",
            "emails": [
                {
                    "id": "bkp_email_101",
                    "subject": "Restored Backup Subject",
                    "sender": "backup@company.com",
                    "body": "Restored contents from backup file.",
                    "importance": "high",
                    "importance_score": 0.95
                }
            ],
            "reminders": [
                {
                    "id": "bkp_rem_101",
                    "title": "Restored Follow Up",
                    "due_at": "2026-08-20T10:00:00Z"
                }
            ]
        }
        res = client.post("/api/backup/import", json=sample_backup)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["restored_emails"], 1)

        # Verify email exists in DB
        restored = db.get_email_by_id("bkp_email_101")
        self.assertIsNotNone(restored)
        self.assertEqual(restored["subject"], "Restored Backup Subject")


class TestAutoTaggingEngine(unittest.TestCase):
    def test_detect_tags_function(self):
        from importance_engine import detect_tags
        tags1 = detect_tags("URGENT: Please confirm project deadline", "Action required ASAP by end of day.")
        self.assertIn("🏷️ Action Needed", tags1)

        tags2 = detect_tags("Interview Shortlist Confirmation", "You have been shortlisted for recruiter call.")
        self.assertIn("💼 Interview", tags2)

        tags3 = detect_tags("Monthly Subscription Invoice", "Payment receipt attached for your billing record.")
        self.assertIn("💳 Financial", tags3)

        tags4 = detect_tags("Security Alert: Password Reset", "Unauthorized login attempt detected from new IP.")
        self.assertIn("⚠️ Security", tags4)

    def test_classification_attaches_tags(self):
        from models import Email, Preferences
        from importance_engine import classify_email

        from datetime import datetime, timezone
        email = Email(
            id="tag_test_001",
            user_id="local_user",
            source="test",
            sender="hr@company.com",
            subject="Job Interview Shortlist & Schedule Call",
            body="We would like to invite you for an interview.",
            received_at=datetime.now(timezone.utc)
        )
        prefs = Preferences(user_id="local_user")
        classified = classify_email(email, prefs)
        self.assertIn("💼 Interview", classified.tags)


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING MAIL EXPERT AI AUTOMATED TEST SUITE")
    print("=" * 70)
    unittest.main()
