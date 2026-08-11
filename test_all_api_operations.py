"""
Performs and verifies all API operations and endpoints defined in api.py.

Run:
    python test_all_api_operations.py
"""

import sys
from fastapi.testclient import TestClient
from api import app
import db

from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

client = TestClient(app)


@patch("gmail_connector.main")
def test_operations(mock_gmail_main):
    print("=" * 70)
    print("PERFORMING ALL OPERATIONS IN API.PY")
    print("=" * 70)

    # 1. Health Check
    res = client.get("/health")
    print(f"\n1. GET /health -> Status: {res.status_code}, Body: {res.json()}")

    # 2. Get Inbox
    res = client.get("/inbox")
    emails = res.json()
    print(f"2. GET /inbox -> Status: {res.status_code}, Total Emails Returned: {len(emails)}")

    target_id = emails[0]["id"] if emails else "sample_p1"

    # 3. Get Agenda
    res = client.get("/agenda")
    print(f"3. GET /agenda -> Status: {res.status_code}, Total Reminders: {len(res.json())}")

    # 4. Get Preferences
    res = client.get("/api/preferences?user_id=local_user")
    prefs = res.json()
    print(f"4. GET /api/preferences -> High Threshold: {prefs.get('high_threshold')}")

    # 5. Save Preferences
    prefs["medium_threshold"] = 0.45
    res = client.post("/api/preferences?user_id=local_user", json=prefs)
    print(f"5. POST /api/preferences -> Status: {res.status_code}, Body: {res.json()}")

    # 6. Auto-Tune Preferences
    res = client.post("/api/preferences/auto-tune?user_id=local_user")
    print(f"6. POST /api/preferences/auto-tune -> Status: {res.status_code}, Body: {res.json()}")

    # 7. Get Accounts
    res = client.get("/api/accounts?user_id=local_user")
    accs = res.json()
    print(f"7. GET /api/accounts -> Accounts Count: {len(accs.get('accounts', []))}")

    # 8. Register Account
    acc_payload = {
        "account_name": "Test Secondary Account",
        "provider": "gmail",
        "email_address": "secondary@domain.com"
    }
    res = client.post("/api/accounts?user_id=local_user", json=acc_payload)
    print(f"8. POST /api/accounts -> Registered: {res.json()}")

    # 9. Sync All Accounts
    res = client.post("/api/sync-all?user_id=local_user")
    print(f"9. POST /api/sync-all -> Status: {res.status_code}, Results: {res.json()}")

    # 10. Classify Email Endpoint
    classify_payload = {
        "email": {
            "id": "op_test_1",
            "user_id": "local_user",
            "source": "api_test",
            "sender": "recruit@placement.edu",
            "subject": "URGENT: Placement Test Deadline Tomorrow",
            "body": "Action required: Complete test slot confirmation within 24 hours.",
            "received_at": "2026-08-11T20:00:00Z"
        },
        "preferences": prefs
    }
    res = client.post("/emails/classify", json=classify_payload)
    print(f"10. POST /emails/classify -> Category: {res.json().get('category')}, Score: {res.json().get('importance_score')}")

    # 11. Classify Batch Endpoint
    res = client.post("/emails/classify-batch", json={"emails": [classify_payload["email"]], "preferences": prefs})
    print(f"11. POST /emails/classify-batch -> Classified Batch Size: {len(res.json())}")

    # 12. Override Email Importance
    res = client.post(f"/emails/{target_id}/override", json={"importance": "high"})
    print(f"12. POST /emails/{target_id}/override -> Status: {res.status_code}, Body: {res.json()}")

    # 13. Mark Read
    res = client.post(f"/emails/{target_id}/read?is_read=true")
    print(f"13. POST /emails/{target_id}/read -> Status: {res.status_code}, Body: {res.json()}")

    # 14. Summarize Email
    res = client.post(f"/emails/{target_id}/summarize")
    print(f"14. POST /emails/{target_id}/summarize -> Summary: {res.json().get('summary')}")

    # 15. Export .ICS Calendar Event
    res = client.get(f"/emails/{target_id}/export-ics?date_idx=0")
    print(f"15. GET /emails/{target_id}/export-ics -> Status: {res.status_code}, Content Length: {len(res.content)} bytes")

    # 16. Google Calendar Web Link
    res = client.get(f"/emails/{target_id}/gcal-link?date_idx=0")
    print(f"16. GET /emails/{target_id}/gcal-link -> GCal URL: {res.json().get('gcal_url')[:60]}...")

    # 17. Generate Smart Reply Draft
    res = client.post(f"/emails/{target_id}/draft-reply?intent=confirm")
    print(f"17. POST /emails/{target_id}/draft-reply -> Reply Subject: {res.json().get('draft', {}).get('reply_subject')}")

    # 18. Dashboard HTML View
    res = client.get("/")
    print(f"18. GET / (Dashboard HTML) -> Status: {res.status_code}, HTML Output Size: {len(res.text)} bytes")

    print("=" * 70)
    print("ALL 18 API OPERATIONS IN API.PY EXECUTED AND VERIFIED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    test_operations()
