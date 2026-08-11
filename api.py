"""
Minimal FastAPI surface for the importance engine.
Run with:  uvicorn api:app --reload
Then POST to /emails/classify or /emails/classify-batch.

This is intentionally storage-agnostic for the MVP: it takes a Preferences
object in the request body rather than loading it from a DB, so you can drop
in whatever persistence layer you like later (Postgres, Firestore, etc.)
without touching the scoring logic.
"""

from __future__ import annotations
from typing import List
from datetime import datetime, timezone

import json  # <--- Add this import
from fastapi import FastAPI, Request, HTTPException
# ... rest of your existing imports
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import Email, Preferences, Reminder
from importance_engine import classify_email, batch_classify
import db

app = FastAPI(title="Mail Expert AI — Triage Service")
db.init_db()

# Needed so the browser extension (running as a chrome-extension:// origin)
# and your phone's browser (a different device on the LAN) are both allowed
# to fetch this local API. Safe here because this server only ever runs on
# your own machine/network for your own use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# PWA support — lets your phone "install" this as an app-like icon on the
# home screen instead of just bookmarking a browser tab.
# ---------------------------------------------------------------------------

@app.get("/manifest.json")
def pwa_manifest():
    return JSONResponse({
        "name": "Mail Expert AI",
        "short_name": "MailExpert",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#111111",
        "theme_color": "#111111",
        "icons": [
            {"src": "/icon192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon512.png", "sizes": "512x512", "type": "image/png"},
        ],
    })


@app.get("/icon192.png")
def icon192():
    return FileResponse("icon192.png")


@app.get("/icon512.png")
def icon512():
    return FileResponse("icon512.png")


@app.get("/service-worker.js")
def service_worker():
    # Minimal service worker: no offline caching (this app needs a live
    # connection to your PC anyway), just enough presence to satisfy
    # Android's "installable" criteria so the home-screen icon behaves
    # like an app instead of a plain bookmark.
    js = "self.addEventListener('fetch', () => {});"
    return Response(content=js, media_type="application/javascript")


class ClassifyRequest(BaseModel):
    email: Email
    preferences: Preferences


class ClassifyBatchRequest(BaseModel):
    emails: List[Email]
    preferences: Preferences


@app.post("/emails/classify", response_model=Email)
def classify_single(req: ClassifyRequest):
    return classify_email(req.email, req.preferences)


@app.post("/emails/classify-batch", response_model=List[Email])
def classify_batch(req: ClassifyBatchRequest):
    return batch_classify(req.emails, req.preferences)


@app.post("/emails/{email_id}/reminders", response_model=List[Reminder])
def generate_reminders(email_id: str, email: Email):
    """
    Turns an already-classified email's extracted_dates into Reminder objects.
    Offsets scale with importance: HIGH gets an extra early warning.
    """
    reminders: List[Reminder] = []
    for i, d in enumerate(email.extracted_dates):
        offsets = [24 * 60, 60]  # 24h and 1h before, in minutes
        if email.importance == "high":
            offsets = [7 * 24 * 60, 24 * 60, 60]  # +1 week heads-up for high importance
        reminders.append(Reminder(
            id=f"{email_id}-rem-{i}",
            email_id=email_id,
            user_id=email.user_id,
            title=f"{d.label}: {email.subject}",
            due_at=d.datetime_utc,
            notify_offsets_minutes=offsets,
            channels=["push", "in_app"],
        ))
    return reminders


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


# ---------------------------------------------------------------------------
# DB-backed inbox endpoints — these read what gmail_connector.py saved.
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats(user_id: str = "local_user"):
    emails = db.get_all_emails(user_id)
    reminders = db.get_upcoming_reminders(user_id)
    return {
        "total_emails": len(emails),
        "high": sum(1 for e in emails if e.get("importance") == "high"),
        "medium": sum(1 for e in emails if e.get("importance") == "medium"),
        "low": sum(1 for e in emails if e.get("importance") == "low"),
        "unread": sum(1 for e in emails if not e.get("is_read")),
        "upcoming_reminders": len(reminders),
    }


@app.get("/inbox")
def get_inbox(user_id: str = "local_user", importance: str | None = None):
    """Returns saved, classified emails as JSON, highest priority first."""
    return db.get_all_emails(user_id, importance_filter=importance)


@app.get("/agenda")
def get_agenda(user_id: str = "local_user"):
    """Upcoming reminders, soonest first — the basis of an agenda/calendar view."""
    return db.get_upcoming_reminders(user_id)


@app.post("/reminders/{reminder_id}/dismiss")
def dismiss_reminder(reminder_id: str):
    db.dismiss_reminder(reminder_id)
    return {"ok": True}


@app.post("/emails/{email_id}/read")
def mark_email_read(email_id: str, is_read: bool = True):
    db.mark_read(email_id, is_read)
    return {"ok": True}


@app.post("/api/sync")
def sync_gmail_api():
    """Triggers Gmail connector to fetch latest emails and update SQLite."""
    import gmail_connector
    try:
        gmail_connector.main()
        return {"status": "success"}
    except Exception as err:
        return {"status": "error", "message": str(err)}


class OverrideRequest(BaseModel):
    importance: str  # "high" | "medium" | "low"


@app.post("/emails/{email_id}/override")
def override_importance(email_id: str, req: OverrideRequest):
    """Lets you manually correct the tier when the engine gets it wrong —
    this is also the data Phase 2's 'learning over time' would train on."""
    db.set_user_override(email_id, req.importance)
    return {"ok": True}

@app.delete("/emails/{email_id}")
def delete_email(email_id: str):
    """Deletes an email from the database by its ID."""
    db.delete_email(email_id)  # Function in db.py
    return {"status": "success", "deleted_id": email_id}


@app.post("/emails/{email_id}/summarize")
def summarize_email(email_id: str):
    """Triggers on-demand AI summarization & action item extraction for a specific email."""
    import llm_processor
    email_dict = db.get_email_by_id(email_id)
    if not email_dict:
        raise HTTPException(status_code=404, detail="Email not found")
    
    res = llm_processor.generate_email_summary_and_actions(
        email_dict["subject"], email_dict["body"]
    )
    db.update_email_summary(email_id, res["summary"], res["action_items"])
    return {
        "status": "success",
        "email_id": email_id,
        "summary": res["summary"],
        "action_items": res["action_items"]
    }


@app.get("/emails/{email_id}/export-ics")
def export_ics(email_id: str, date_idx: int = 0):
    """Downloads an iCalendar (.ics) event file for an email's deadline."""
    import calendar_processor
    email_dict = db.get_email_by_id(email_id)
    if not email_dict:
        raise HTTPException(status_code=404, detail="Email not found")
    
    cal_data = calendar_processor.export_email_deadline(email_dict, date_idx)
    return Response(
        content=cal_data["ics_content"],
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="deadline-{email_id}.ics"'}
    )


@app.get("/emails/{email_id}/gcal-link")
def get_gcal_link(email_id: str, date_idx: int = 0):
    """Returns a 1-click Google Calendar web creation URL."""
    import calendar_processor
    email_dict = db.get_email_by_id(email_id)
    if not email_dict:
        raise HTTPException(status_code=404, detail="Email not found")
    
    cal_data = calendar_processor.export_email_deadline(email_dict, date_idx)
    return {"status": "success", "gcal_url": cal_data["gcal_url"], "title": cal_data["title"]}


@app.post("/emails/{email_id}/draft-reply")
def draft_reply_endpoint(email_id: str, intent: str = "confirm"):
    """Generates an AI / template response draft for an email."""
    import draft_processor
    email_dict = db.get_email_by_id(email_id)
    if not email_dict:
        raise HTTPException(status_code=404, detail="Email not found")
    
    draft = draft_processor.generate_reply_draft(
        subject=email_dict["subject"],
        body=email_dict["body"],
        sender=email_dict["sender"],
        intent=intent
    )
    return {"status": "success", "email_id": email_id, "draft": draft}


class AccountRegisterRequest(BaseModel):
    account_name: str
    provider: str = "gmail"        # gmail | imap | outlook | mock
    email_address: str


@app.get("/api/accounts")
def get_user_accounts_endpoint(user_id: str = "local_user"):
    import multi_inbox_connector
    connector = multi_inbox_connector.MultiInboxConnector(user_id)
    return {"accounts": connector.get_accounts()}


@app.post("/api/accounts")
def register_account_endpoint(req: AccountRegisterRequest, user_id: str = "local_user"):
    import multi_inbox_connector
    connector = multi_inbox_connector.MultiInboxConnector(user_id)
    return connector.register_account(
        account_name=req.account_name,
        provider=req.provider,
        email_address=req.email_address
    )


@app.delete("/api/accounts/{account_id}")
def delete_account_endpoint(account_id: str, user_id: str = "local_user"):
    import multi_inbox_connector
    connector = multi_inbox_connector.MultiInboxConnector(user_id)
    connector.delete_account(account_id)
    return {"status": "success", "deleted_id": account_id}


@app.post("/api/sync-all")
def sync_all_accounts_endpoint(user_id: str = "local_user"):
    import multi_inbox_connector
    connector = multi_inbox_connector.MultiInboxConnector(user_id)
    return connector.sync_all_accounts()


# ---------------------------------------------------------------------------
# Preferences — JSON API + HTML form
# ---------------------------------------------------------------------------

@app.get("/api/preferences")
def get_preferences_json(user_id: str = "local_user"):
    return db.get_preferences_dict(user_id)


@app.post("/api/preferences/auto-tune")
def auto_tune_preferences_endpoint(user_id: str = "local_user"):
    """Auto-tunes category weight preferences based on user override history."""
    import learning_processor
    return learning_processor.auto_tune_user_preferences(user_id)


@app.post("/api/preferences")
async def save_preferences_json(request: Request, user_id: str = "local_user"):
    body = await request.json()
    db.save_preferences_dict(user_id, body)
    return {"ok": True}


@app.get("/preferences", response_class=HTMLResponse)
def preferences_page(user_id: str = "local_user"):
    prefs = db.get_preferences_dict(user_id)
    sender_rules_rows = "".join(f"""
        <div class="rule-row">
          <input class="sender-input" value="{r['sender']}" placeholder="sender@example.com or @domain.com">
          <select class="action-select">
            <option value="always_high" {'selected' if r['action']=='always_high' else ''}>Always High</option>
            <option value="always_low" {'selected' if r['action']=='always_low' else ''}>Always Low</option>
            <option value="mute" {'selected' if r['action']=='mute' else ''}>Mute</option>
          </select>
          <button onclick="this.parentElement.remove()">Remove</button>
        </div>
    """ for r in prefs["sender_rules"])

    cat_rows = "".join(f"""
        <div class="rule-row">
          <label style="width:120px">{cat}</label>
          <input type="number" step="0.05" min="0" max="1" class="cat-weight" data-cat="{cat}" value="{weight}">
        </div>
    """ for cat, weight in prefs["category_weights"].items())

    return f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
      <title>Mail Expert AI — Preferences</title>
      <link rel="manifest" href="/manifest.json">
      <link rel="apple-touch-icon" href="/icon192.png">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black">
      <meta name="theme-color" content="#111111">
      <style>
        body {{ font-family: -apple-system, Arial, sans-serif; background:#111; color:#eee;
                padding:16px; max-width:700px; margin:0 auto; }}
        h1 {{ font-size: 20px; }}
        h2 {{ font-size: 15px; color:#ccc; margin-top:28px; }}
        .rule-row {{ display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }}
        input, select {{ background:#1b1b1b; color:#eee; border:1px solid #444; border-radius:4px;
                          padding:8px 10px; font-size:15px; }}
        .sender-input {{ flex:1; min-width:150px; }}
        button {{ background:#2b2b2b; color:#eee; border:1px solid #444; border-radius:4px;
                   padding:8px 14px; cursor:pointer; font-size:14px; }}
        button:hover {{ background:#3a3a3a; }}
        .save-btn {{ background:#2ecc71; color:#111; font-weight:bold; margin-top:20px; width:100%; padding:12px; }}
        a {{ color:#3498db; }}
        @media (max-width: 480px) {{
          .rule-row label {{ width:100% !important; }}
        }}
      </style>
    </head>
    <body>
      <a href="/">← Back to inbox</a>
      <h1>⚙️ Preferences</h1>

      <h2>Sender rules</h2>
      <div id="sender-rules">{sender_rules_rows}</div>
      <button onclick="addSenderRule()">+ Add sender rule</button>

      <h2>Category importance weights (0–1)</h2>
      <div id="cat-weights">{cat_rows}</div>

      <h2>Tier thresholds</h2>
      <div class="rule-row"><label style="width:120px">High tier at score ≥</label>
        <input type="number" step="0.05" min="0" max="1" id="high-threshold" value="{prefs['high_threshold']}"></div>
      <div class="rule-row"><label style="width:120px">Medium tier at score ≥</label>
        <input type="number" step="0.05" min="0" max="1" id="medium-threshold" value="{prefs['medium_threshold']}"></div>

      <button class="save-btn" onclick="savePrefs()">Save preferences</button>
      <span id="save-status" style="margin-left:10px;color:#2ecc71;"></span>

      <script>
        function addSenderRule() {{
          const div = document.createElement('div');
          div.className = 'rule-row';
          div.innerHTML = `
            <input class="sender-input" placeholder="sender@example.com or @domain.com">
            <select class="action-select">
              <option value="always_high">Always High</option>
              <option value="always_low">Always Low</option>
              <option value="mute">Mute</option>
            </select>
            <button onclick="this.parentElement.remove()">Remove</button>`;
          document.getElementById('sender-rules').appendChild(div);
        }}

        async function savePrefs() {{
          const senderRules = [...document.querySelectorAll('#sender-rules .rule-row')]
            .map(row => ({{
              sender: row.querySelector('.sender-input').value.trim(),
              action: row.querySelector('.action-select').value
            }}))
            .filter(r => r.sender.length > 0);

          const categoryWeights = {{}};
          document.querySelectorAll('.cat-weight').forEach(inp => {{
            categoryWeights[inp.dataset.cat] = parseFloat(inp.value);
          }});

          const payload = {{
            timezone: "UTC",
            sender_rules: senderRules,
            category_weights: categoryWeights,
            high_threshold: parseFloat(document.getElementById('high-threshold').value),
            medium_threshold: parseFloat(document.getElementById('medium-threshold').value),
          }};

          await fetch('/api/preferences', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(payload)
          }});
          document.getElementById('save-status').textContent = 'Saved ✓';
          setTimeout(() => document.getElementById('save-status').textContent = '', 2000);
        }}
      </script>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# HTML Dashboard & UI Layer
# ---------------------------------------------------------------------------

TIER_COLORS = {"high": "#ef4444", "medium": "#f59e0b", "low": "#10b981"}


@app.get("/", response_class=HTMLResponse)
def dashboard(user_id: str = "local_user"):
    emails = db.get_all_emails(user_id)
    reminders = db.get_upcoming_reminders(user_id)
    prefs = db.get_preferences_dict(user_id)
    accounts = db.get_user_accounts(user_id)
    if not accounts:
        db.upsert_account("acc_primary", user_id, "Primary Account", "gmail", "user@local.app")
        accounts = db.get_user_accounts(user_id)

    high_count = sum(1 for e in emails if e.get("importance") == "high")
    medium_count = sum(1 for e in emails if e.get("importance") == "medium")
    low_count = sum(1 for e in emails if e.get("importance") == "low")
    unread_count = sum(1 for e in emails if not e.get("is_read"))

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
      <title>Mail Expert AI — Priority App</title>
      <link rel="manifest" href="/manifest.json">
      <link rel="apple-touch-icon" href="/icon192.png">
      <meta name="theme-color" content="#0b0f19">
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
      <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
        :root {{
          --bg-dark: #0b0f19;
          --card-bg: rgba(21, 29, 48, 0.7);
          --card-border: rgba(255, 255, 255, 0.08);
          --text-main: #f8fafc;
          --text-muted: #94a3b8;
          --high-color: #ef4444;
          --medium-color: #f59e0b;
          --low-color: #10b981;
          --accent-blue: #38bdf8;
        }}
        body {{
          background-color: var(--bg-dark);
          color: var(--text-main);
          min-height: 100vh;
          padding-bottom: 80px;
          background-image: 
            radial-gradient(at 10% 10%, rgba(56, 189, 248, 0.08) 0px, transparent 50%),
            radial-gradient(at 90% 90%, rgba(239, 68, 68, 0.08) 0px, transparent 50%);
          background-attachment: fixed;
        }}
        .app-container {{
          max-width: 900px;
          margin: 0 auto;
          padding: 16px;
        }}
        header {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 16px 0;
          margin-bottom: 16px;
          border-bottom: 1px solid var(--card-border);
        }}
        .brand {{
          display: flex;
          align-items: center;
          gap: 12px;
        }}
        .brand-logo {{
          width: 42px;
          height: 42px;
          background: linear-gradient(135deg, #38bdf8, #818cf8);
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 22px;
          box-shadow: 0 4px 15px rgba(56, 189, 248, 0.3);
        }}
        .brand-title {{
          font-size: 20px;
          font-weight: 800;
          letter-spacing: -0.02em;
          background: linear-gradient(135deg, #ffffff, #cbd5e1);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }}
        .brand-subtitle {{
          font-size: 11px;
          color: var(--accent-blue);
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }}
        .header-actions {{
          display: flex;
          gap: 8px;
          align-items: center;
        }}
        .btn {{
          background: rgba(30, 41, 59, 0.8);
          color: var(--text-main);
          border: 1px solid var(--card-border);
          padding: 8px 14px;
          border-radius: 10px;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s ease;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          backdrop-filter: blur(10px);
        }}
        .btn:hover {{
          background: rgba(51, 65, 85, 0.9);
          transform: translateY(-1px);
        }}
        .btn-primary {{
          background: linear-gradient(135deg, #38bdf8, #2563eb);
          color: #fff;
          border: none;
          box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
        }}

        /* Hero Metrics Grid */
        .stats-grid {{
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 12px;
          margin-bottom: 20px;
        }}
        .stat-card {{
          background: var(--card-bg);
          border: 1px solid var(--card-border);
          border-radius: 14px;
          padding: 14px;
          backdrop-filter: blur(16px);
          display: flex;
          flex-direction: column;
          gap: 6px;
          transition: transform 0.2s ease;
        }}
        .stat-card:hover {{ transform: translateY(-2px); }}
        .stat-val {{ font-size: 24px; font-weight: 800; }}
        .stat-label {{ font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; }}

        /* Filter Tabs & Search Bar */
        .controls-section {{
          display: flex;
          flex-direction: column;
          gap: 12px;
          margin-bottom: 20px;
        }}
        .tabs-bar {{
          display: flex;
          background: rgba(15, 23, 42, 0.6);
          padding: 4px;
          border-radius: 12px;
          border: 1px solid var(--card-border);
          overflow-x: auto;
          gap: 4px;
        }}
        .tab-btn {{
          background: transparent;
          border: none;
          color: var(--text-muted);
          padding: 8px 16px;
          font-size: 13px;
          font-weight: 600;
          border-radius: 8px;
          cursor: pointer;
          white-space: nowrap;
          transition: all 0.2s;
        }}
        .tab-btn.active {{
          background: var(--accent-blue);
          color: #0f172a;
          box-shadow: 0 2px 8px rgba(56, 189, 248, 0.4);
        }}
        .search-and-cats {{
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }}
        .search-input-wrap {{
          flex: 1;
          min-width: 220px;
          position: relative;
        }}
        .search-input-wrap input {{
          width: 100%;
          background: var(--card-bg);
          border: 1px solid var(--card-border);
          color: var(--text-main);
          padding: 10px 14px 10px 38px;
          border-radius: 10px;
          font-size: 13px;
          outline: none;
          backdrop-filter: blur(10px);
        }}
        .search-input-wrap input:focus {{ border-color: var(--accent-blue); }}
        .search-icon {{ position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 14px; }}
        .category-pills {{ display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }}
        .cat-pill {{
          background: rgba(30, 41, 59, 0.6);
          border: 1px solid var(--card-border);
          color: var(--text-muted);
          padding: 6px 12px;
          border-radius: 20px;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          white-space: nowrap;
        }}
        .cat-pill.active {{ background: rgba(56, 189, 248, 0.2); color: var(--accent-blue); border-color: var(--accent-blue); }}

        /* Email Priority Cards */
        .email-card {{
          background: var(--card-bg);
          border: 1px solid var(--card-border);
          border-left: 5px solid var(--low-color);
          border-radius: 14px;
          padding: 16px;
          margin-bottom: 12px;
          backdrop-filter: blur(16px);
          transition: transform 0.15s ease, box-shadow 0.15s ease;
          position: relative;
        }}
        .email-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }}
        .email-card.high {{ border-left-color: var(--high-color); box-shadow: 0 0 15px rgba(239, 68, 68, 0.05); }}
        .email-card.medium {{ border-left-color: var(--medium-color); }}
        .email-card.low {{ border-left-color: var(--low-color); }}
        .email-card.read {{ opacity: 0.65; }}

        .card-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }}
        .badges-group {{ display: flex; align-items: center; gap: 8px; }}
        .tier-badge {{
          font-size: 10px;
          font-weight: 800;
          padding: 4px 10px;
          border-radius: 20px;
          color: #fff;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }}
        .tier-badge.high {{ background: var(--high-color); box-shadow: 0 2px 8px rgba(239, 68, 68, 0.4); }}
        .tier-badge.medium {{ background: var(--medium-color); box-shadow: 0 2px 8px rgba(245, 158, 11, 0.4); }}
        .tier-badge.low {{ background: var(--low-color); box-shadow: 0 2px 8px rgba(16, 185, 129, 0.4); }}

        .cat-tag {{ font-size: 11px; background: rgba(255,255,255,0.06); padding: 3px 8px; border-radius: 6px; color: #cbd5e1; text-transform: capitalize; }}
        .score-pill {{ font-size: 12px; font-weight: 700; color: var(--accent-blue); background: rgba(56, 189, 248, 0.1); padding: 3px 8px; border-radius: 6px; }}

        .email-subject {{ font-size: 16px; font-weight: 700; color: var(--text-main); margin-bottom: 4px; line-height: 1.35; }}
        .email-sender {{ font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }}
        .email-snippet {{ font-size: 13px; color: #cbd5e1; line-height: 1.5; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}

        .deadline-box {{
          background: rgba(245, 158, 11, 0.12);
          border: 1px solid rgba(245, 158, 11, 0.3);
          border-radius: 10px;
          padding: 8px 12px;
          margin-bottom: 12px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }}
        .deadline-text {{ font-size: 13px; color: #fbbf24; font-weight: 600; display: flex; align-items: center; gap: 6px; }}

        .ai-summary-box {{
          background: rgba(56, 189, 248, 0.08);
          border: 1px solid rgba(56, 189, 248, 0.25);
          border-radius: 10px;
          padding: 10px 14px;
          margin-bottom: 12px;
          font-size: 13px;
          color: #e2e8f0;
          line-height: 1.45;
        }}
        .ai-summary-title {{ font-weight: 700; color: var(--accent-blue); margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }}
        .action-items-list {{ margin-top: 6px; padding-left: 18px; color: #cbd5e1; font-size: 12px; }}
        .action-items-list li {{ margin-bottom: 3px; }}

        .card-actions {{ display: flex; gap: 8px; flex-wrap: wrap; pt: 8px; border-top: 1px solid rgba(255,255,255,0.06); }}
        .action-sm {{
          background: rgba(15, 23, 42, 0.8);
          border: 1px solid var(--card-border);
          color: var(--text-muted);
          padding: 5px 10px;
          border-radius: 6px;
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
        }}
        .action-sm:hover {{ background: rgba(51, 65, 85, 0.8); color: var(--text-main); }}

        /* Score Breakdown Modal */
        .modal-overlay {{
          position: fixed; top: 0; left: 0; right: 0; bottom: 0;
          background: rgba(0,0,0,0.75);
          backdrop-filter: blur(8px);
          display: none;
          align-items: center;
          justify-content: center;
          z-index: 9999;
          padding: 16px;
        }}
        .modal-content {{
          background: #151d30;
          border: 1px solid var(--card-border);
          border-radius: 16px;
          padding: 24px;
          max-width: 480px;
          width: 100%;
          color: var(--text-main);
        }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
        .modal-title {{ font-size: 17px; font-weight: 700; }}
        .close-modal {{ background: none; border: none; color: var(--text-muted); font-size: 20px; cursor: pointer; }}
        .breakdown-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 13px; }}

        /* Rules & Settings Tab */
        .settings-card {{
          background: var(--card-bg);
          border: 1px solid var(--card-border);
          border-radius: 14px;
          padding: 20px;
          margin-bottom: 16px;
          backdrop-filter: blur(16px);
        }}
        .settings-title {{ font-size: 16px; font-weight: 700; margin-bottom: 14px; color: var(--accent-blue); }}
        .rule-item {{ display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; align-items: center; }}
        .rule-item input, .rule-item select {{
          background: rgba(15, 23, 42, 0.8);
          border: 1px solid var(--card-border);
          color: var(--text-main);
          padding: 8px 12px;
          border-radius: 8px;
          font-size: 13px;
        }}
        .rule-item input {{ flex: 1; min-width: 150px; }}

        /* Mobile Bottom Nav */
        .bottom-nav {{
          display: none;
          position: fixed;
          bottom: 0; left: 0; right: 0;
          background: rgba(11, 15, 25, 0.95);
          backdrop-filter: blur(20px);
          border-top: 1px solid var(--card-border);
          padding: 8px 16px;
          justify-content: space-around;
          z-index: 100;
        }}
        .nav-item {{
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 2px;
          color: var(--text-muted);
          font-size: 10px;
          font-weight: 600;
          background: none;
          border: none;
          cursor: pointer;
        }}
        .nav-item.active {{ color: var(--accent-blue); }}
        .nav-item-icon {{ font-size: 20px; }}

        @media (max-width: 768px) {{
          .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
          .bottom-nav {{ display: flex; }}
          body {{ padding-bottom: 90px; }}
        }}
      </style>
    </head>
    <body>
      <div class="app-container">
        <header>
          <div class="brand">
            <div class="brand-logo">📬</div>
            <div>
              <div class="brand-title">Mail Expert AI</div>
              <div class="brand-subtitle">Smart Priority App</div>
            </div>
          </div>
          <div class="header-actions">
            <button class="btn" id="install-btn" style="display: none; background: var(--accent-blue); color: #0f172a;">⬇️ Install App</button>
            <button class="btn" onclick="syncGmail()"><span id="sync-icon">🔄</span> Sync Gmail</button>
          </div>
        </header>

        <!-- Hero Stats Cards -->
        <div class="stats-grid">
          <div class="stat-card" style="border-top: 3px solid var(--high-color);">
            <div class="stat-val" style="color: var(--high-color);">{high_count}</div>
            <div class="stat-label">🔥 High Priority</div>
          </div>
          <div class="stat-card" style="border-top: 3px solid var(--medium-color);">
            <div class="stat-val" style="color: var(--medium-color);">{medium_count}</div>
            <div class="stat-label">⚡ Medium Priority</div>
          </div>
          <div class="stat-card" style="border-top: 3px solid var(--accent-blue);">
            <div class="stat-val" style="color: var(--accent-blue);">{len(reminders)}</div>
            <div class="stat-label">⏰ Deadlines Soon</div>
          </div>
          <div class="stat-card" style="border-top: 3px solid var(--low-color);">
            <div class="stat-val">{unread_count}</div>
            <div class="stat-label">📩 Unread Emails</div>
          </div>
        </div>

        <!-- Filters & Search Bar -->
        <div class="controls-section">
          <div class="tabs-bar">
            <button class="tab-btn active" id="tab-all" onclick="setMainTab('all')">All Inbox ({len(emails)})</button>
            <button class="tab-btn" id="tab-high" onclick="setMainTab('high')">🔥 High ({high_count})</button>
            <button class="tab-btn" id="tab-medium" onclick="setMainTab('medium')">⚡ Medium ({medium_count})</button>
            <button class="tab-btn" id="tab-low" onclick="setMainTab('low')">💤 Low ({low_count})</button>
            <button class="tab-btn" id="tab-agenda" onclick="setMainTab('agenda')">📅 Agenda ({len(reminders)})</button>
            <button class="tab-btn" id="tab-settings" onclick="setMainTab('settings')">⚙️ Rules & Settings</button>
          </div>

          <div class="search-and-cats" id="search-bar-wrap">
            <div class="search-input-wrap">
              <span class="search-icon">🔍</span>
              <input type="text" id="search-input" placeholder="Search priority emails, senders, deadlines..." oninput="renderInbox()">
            </div>
            <div class="category-pills">
              <button class="cat-pill active" onclick="setCategoryFilter('all', this)">All</button>
              <button class="cat-pill" onclick="setCategoryFilter('placement', this)">Placement</button>
              <button class="cat-pill" onclick="setCategoryFilter('industry', this)">Industry</button>
              <button class="cat-pill" onclick="setCategoryFilter('club', this)">Club</button>
              <button class="cat-pill" onclick="setCategoryFilter('event', this)">Event</button>
            </div>
          </div>
        </div>

        <!-- Main Views Container -->
        <div id="inbox-view">
          <div id="emails-container"></div>
        </div>

        <div id="agenda-view" style="display: none;">
          <div id="agenda-container"></div>
        </div>

        <div id="settings-view" style="display: none;">
          <div class="settings-card">
            <div class="settings-title">⚙️ Sender Priority Rules</div>
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
              Hard overrides for key senders. `Always High` forces score to 1.0, `Mute` hides the email.
            </p>
            <div id="sender-rules-list"></div>
            <button class="btn" style="margin-top: 10px;" onclick="addSenderRuleRow()">+ Add Sender Rule</button>
          </div>

          <div class="settings-card">
            <div class="settings-title">📊 Category Importance Multipliers (0.0 to 1.0)</div>
            <div id="category-weights-list"></div>
            <button class="btn" style="margin-top: 12px; background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); width: 100%; justify-content: center;" onclick="autoTunePreferences()">🧠 Auto-Tune Weights from Override Activity</button>
          </div>

          <div class="settings-card">
            <div class="settings-title">🎯 Tier Thresholds</div>
            <div class="rule-item">
              <label style="width: 140px; font-size:13px;">High Tier Score ≥</label>
              <input type="number" id="high-thresh-inp" step="0.05" min="0" max="1" value="{prefs['high_threshold']}">
            </div>
            <div class="rule-item">
              <label style="width: 140px; font-size:13px;">Medium Tier Score ≥</label>
              <input type="number" id="med-thresh-inp" step="0.05" min="0" max="1" value="{prefs['medium_threshold']}">
            </div>
            <button class="btn btn-primary" style="margin-top: 14px; width: 100%; justify-content: center;" onclick="savePreferences()">Save Rules & Settings</button>
            <div id="settings-status" style="margin-top: 8px; font-size: 12px; color: var(--low-color); text-align: center;"></div>
          </div>
        </div>
      </div>

      <!-- Score Breakdown Modal -->
      <div class="modal-overlay" id="score-modal">
        <div class="modal-content">
          <div class="modal-header">
            <div class="modal-title">📊 Score Breakdown</div>
            <button class="close-modal" onclick="closeScoreModal()">&times;</button>
          </div>
          <div id="modal-body-content"></div>
        </div>
      </div>

      <!-- Smart Reply Modal -->
      <div class="modal-overlay" id="draft-modal">
        <div class="modal-content">
          <div class="modal-header">
            <div class="modal-title">✍️ Smart Reply Generator</div>
            <button class="close-modal" onclick="closeDraftModal()">&times;</button>
          </div>
          <div style="margin-bottom: 12px;">
            <label style="font-size: 12px; color: var(--text-muted); display: block; margin-bottom: 6px;">Select Response Intent:</label>
            <div style="display: flex; gap: 6px; flex-wrap: wrap;">
              <button class="action-sm" onclick="generateReplyDraft('confirm')">✅ Confirm Slot</button>
              <button class="action-sm" onclick="generateReplyDraft('extension')">⏳ Ask Extension</button>
              <button class="action-sm" onclick="generateReplyDraft('accept')">🎉 Accept Offer</button>
              <button class="action-sm" onclick="generateReplyDraft('decline')">❌ Decline</button>
              <button class="action-sm" onclick="generateReplyDraft('clarification')">❓ Query</button>
            </div>
          </div>
          <div style="margin-bottom: 10px;">
            <label style="font-size: 12px; color: var(--text-muted);">Subject:</label>
            <input type="text" id="draft-subject-inp" style="width: 100%; background: rgba(15,23,42,0.8); border: 1px solid var(--card-border); color: var(--text-main); padding: 8px; border-radius: 8px; font-size: 13px; margin-top: 4px;">
          </div>
          <div style="margin-bottom: 14px;">
            <label style="font-size: 12px; color: var(--text-muted);">Reply Body:</label>
            <textarea id="draft-body-inp" rows="7" style="width: 100%; background: rgba(15,23,42,0.8); border: 1px solid var(--card-border); color: var(--text-main); padding: 10px; border-radius: 8px; font-size: 13px; margin-top: 4px; line-height: 1.4;"></textarea>
          </div>
          <button class="btn btn-primary" style="width: 100%; justify-content: center;" onclick="copyDraftToClipboard()">📋 Copy Reply to Clipboard</button>
          <div id="copy-status" style="margin-top: 6px; font-size: 12px; color: var(--low-color); text-align: center;"></div>
        </div>
      </div>

      <!-- Mobile Navigation Bar -->
      <div class="bottom-nav">
        <button class="nav-item active" onclick="setMainTab('all')">
          <span class="nav-item-icon">📥</span>
          <span>Inbox</span>
        </button>
        <button class="nav-item" onclick="setMainTab('high')">
          <span class="nav-item-icon">🔥</span>
          <span>Priority</span>
        </button>
        <button class="nav-item" onclick="setMainTab('agenda')">
          <span class="nav-item-icon">📅</span>
          <span>Agenda</span>
        </button>
        <button class="nav-item" onclick="setMainTab('settings')">
          <span class="nav-item-icon">⚙️</span>
          <span>Rules</span>
        </button>
      </div>

      <script>
        const EMAILS = {json.dumps(emails)};
        const REMINDERS = {json.dumps(reminders)};
        const PREFS = {json.dumps(prefs)};
        const ACCOUNTS = {json.dumps(accounts)};

        let currentTab = 'all';
        let currentCat = 'all';

        function setMainTab(tab) {{
          currentTab = tab;
          document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
          const targetBtn = document.getElementById('tab-' + tab);
          if (targetBtn) targetBtn.classList.add('active');

          const inboxView = document.getElementById('inbox-view');
          const agendaView = document.getElementById('agenda-view');
          const settingsView = document.getElementById('settings-view');
          const searchBarWrap = document.getElementById('search-bar-wrap');

          if (tab === 'agenda') {{
            inboxView.style.display = 'none';
            agendaView.style.display = 'block';
            settingsView.style.display = 'none';
            searchBarWrap.style.display = 'none';
            renderAgenda();
          }} else if (tab === 'settings') {{
            inboxView.style.display = 'none';
            agendaView.style.display = 'none';
            settingsView.style.display = 'block';
            searchBarWrap.style.display = 'none';
            renderSettingsForm();
          }} else {{
            inboxView.style.display = 'block';
            agendaView.style.display = 'none';
            settingsView.style.display = 'none';
            searchBarWrap.style.display = 'flex';
            renderInbox();
          }}
        }}

        function setCategoryFilter(cat, btnEl) {{
          currentCat = cat;
          document.querySelectorAll('.cat-pill').forEach(b => b.classList.remove('active'));
          btnEl.classList.add('active');
          renderInbox();
        }}

        function renderInbox() {{
          const query = document.getElementById('search-input').value.toLowerCase().trim();
          const filtered = EMAILS.filter(e => {{
            const matchesTab = currentTab === 'all' || e.importance === currentTab;
            const matchesCat = currentCat === 'all' || e.category.toLowerCase() === currentCat.toLowerCase();
            const matchesQuery = !query || 
              e.subject.toLowerCase().includes(query) || 
              e.sender.toLowerCase().includes(query) || 
              e.body.toLowerCase().includes(query) ||
              e.category.toLowerCase().includes(query);
            return matchesTab && matchesCat && matchesQuery;
          }});

          const container = document.getElementById('emails-container');
          if (!filtered.length) {{
            container.innerHTML = `<div style="text-align: center; padding: 48px 16px; color: var(--text-muted); font-size: 14px;">No emails found matching your active filter.</div>`;
            return;
          }}

          container.innerHTML = filtered.map(e => {{
            const tier = e.importance || 'low';
            const readClass = e.is_read ? 'read' : '';
            const extractedDates = e.extracted_dates || [];
            const actionItems = e.action_items || [];
            
            const datesHtml = extractedDates.map((d, idx) => `
              <div class="deadline-box">
                <div>
                  <div class="deadline-text">⏳ ${{d.label}}: ${{d.datetime_utc.replace('T', ' ').slice(0, 16)}}</div>
                  <span style="font-size: 11px; color: var(--text-muted);">Score +${{(d.confidence * 0.35).toFixed(2)}}</span>
                </div>
                <div style="display: flex; gap: 6px;">
                  <a href="/emails/${{e.id}}/export-ics?date_idx=${{idx}}" class="action-sm" style="text-decoration: none; color: #fbbf24;" download>📅 .ics</a>
                  <button class="action-sm" style="color: var(--accent-blue);" onclick="openGCal('${{e.id}}', ${{idx}})">🌐 GCal</button>
                </div>
              </div>
            `).join('');

            const summaryHtml = e.summary ? `
              <div class="ai-summary-box">
                <div class="ai-summary-title">💡 Executive Summary</div>
                <div>${{escapeHtml(e.summary)}}</div>
                ${{actionItems.length ? `
                  <div style="font-weight: 600; font-size: 11px; color: var(--accent-blue); margin-top: 6px;">Action Items:</div>
                  <ul class="action-items-list">
                    ${{actionItems.map(item => `<li>${{escapeHtml(item)}}</li>`).join('')}}
                  </ul>
                ` : ''}}
              </div>
            ` : '';

            return `
              <div class="email-card ${{tier}} ${{readClass}}">
                <div class="card-top">
                  <div class="badges-group">
                    <span class="tier-badge ${{tier}}">${{tier}}</span>
                    <span class="cat-tag">${{e.category}}</span>
                    <span class="cat-tag" style="background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); font-weight: 600;">📱 ${{e.account_label || 'Primary Account'}}</span>
                  </div>
                  <span class="score-pill">Score: ${{e.importance_score}}</span>
                </div>
                <div class="email-subject">${{escapeHtml(e.subject)}}</div>
                <div class="email-sender">From: <strong>${{escapeHtml(e.sender)}}</strong></div>
                ${{summaryHtml}}
                <div class="email-snippet">${{escapeHtml(e.body)}}</div>
                ${{datesHtml}}
                <div class="card-actions">
                  <button class="action-sm" onclick="toggleRead('${{e.id}}', ${{!e.is_read}})">${{e.is_read ? 'Mark Unread' : 'Mark Read'}}</button>
                  <button class="action-sm" style="color: var(--accent-blue);" onclick="openDraftModal('${{e.id}}')">✍️ Smart Reply</button>
                  <button class="action-sm" onclick="summarizeEmail('${{e.id}}')">🤖 AI Summarize</button>
                  <button class="action-sm" onclick="setOverride('${{e.id}}', 'high')">Mark High</button>
                  <button class="action-sm" onclick="setOverride('${{e.id}}', 'low')">Mark Low</button>
                  <button class="action-sm" style="color: var(--high-color);" onclick="deleteEmail('${{e.id}}')">🗑️ Delete</button>
                  <button class="action-sm" style="margin-left: auto; color: var(--accent-blue);" onclick="openScoreBreakdown('${{e.id}}')">📊 Why this score?</button>
                </div>
              </div>
            `;
          }}).join('');
        }}

        function renderAgenda() {{
          const container = document.getElementById('agenda-container');
          if (!REMINDERS.length) {{
            container.innerHTML = `<div style="text-align: center; padding: 48px 16px; color: var(--text-muted); font-size: 14px;">No upcoming reminders scheduled.</div>`;
            return;
          }}

          container.innerHTML = REMINDERS.map(r => `
            <div class="email-card medium">
              <div class="card-top">
                <div class="badges-group">
                  <span class="tier-badge medium">Deadline</span>
                  <span class="cat-tag">Upcoming</span>
                </div>
                <span class="score-pill">${{r.due_at.replace('T', ' ').slice(0, 16)}}</span>
              </div>
              <div class="email-subject">⏰ ${{escapeHtml(r.title)}}</div>
              <div class="card-actions" style="margin-top: 10px;">
                <button class="action-sm" onclick="dismissReminder('${{r.id}}')">Dismiss Reminder</button>
              </div>
            </div>
          `).join('');
        }}

        function renderSettingsForm() {{
          const rulesContainer = document.getElementById('sender-rules-list');
          const rules = PREFS.sender_rules || [];
          rulesContainer.innerHTML = rules.map((r, i) => `
            <div class="rule-item">
              <input class="sender-inp" value="${{escapeHtml(r.sender)}}" placeholder="sender@example.com or @domain.com">
              <select class="action-sel">
                <option value="always_high" ${{r.action==='always_high'?'selected':''}}>Always High</option>
                <option value="always_low" ${{r.action==='always_low'?'selected':''}}>Always Low</option>
                <option value="mute" ${{r.action==='mute'?'selected':''}}>Mute</option>
              </select>
              <button class="btn" onclick="this.parentElement.remove()">Remove</button>
            </div>
          `).join('');

          const weightsContainer = document.getElementById('category-weights-list');
          const weights = PREFS.category_weights || {{}};
          weightsContainer.innerHTML = Object.entries(weights).map(([cat, w]) => `
            <div class="rule-item">
              <label style="width: 120px; font-size: 13px; text-transform: capitalize;">${{cat}}</label>
              <input type="number" class="cat-weight-inp" data-cat="${{cat}}" step="0.05" min="0" max="1" value="${{w}}">
            </div>
          `).join('');
        }}

        function addSenderRuleRow() {{
          const div = document.createElement('div');
          div.className = 'rule-item';
          div.innerHTML = `
            <input class="sender-inp" placeholder="sender@example.com or @domain.com">
            <select class="action-sel">
              <option value="always_high">Always High</option>
              <option value="always_low">Always Low</option>
              <option value="mute">Mute</option>
            </select>
            <button class="btn" onclick="this.parentElement.remove()">Remove</button>
          `;
          document.getElementById('sender-rules-list').appendChild(div);
        }}

        async function savePreferences() {{
          const senderRules = [...document.querySelectorAll('#sender-rules-list .rule-item')]
            .map(row => ({{
              sender: row.querySelector('.sender-inp').value.trim(),
              action: row.querySelector('.action-sel').value
            }}))
            .filter(r => r.sender.length > 0);

          const categoryWeights = {{}};
          document.querySelectorAll('.cat-weight-inp').forEach(inp => {{
            categoryWeights[inp.dataset.cat] = parseFloat(inp.value);
          }});

          const payload = {{
            timezone: "UTC",
            sender_rules: senderRules,
            category_weights: categoryWeights,
            high_threshold: parseFloat(document.getElementById('high-thresh-inp').value),
            medium_threshold: parseFloat(document.getElementById('med-thresh-inp').value),
          }};

          await fetch('/api/preferences', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify(payload)
          }});

          document.getElementById('settings-status').textContent = 'Saved Preferences Successfully ✓';
          setTimeout(() => document.getElementById('settings-status').textContent = '', 2500);
        }}

        async function autoTunePreferences() {{
          const statusEl = document.getElementById('settings-status');
          if (statusEl) statusEl.textContent = '🧠 Analyzing activity & auto-tuning weights...';
          try {{
            const resp = await fetch('/api/preferences/auto-tune', {{ method: 'POST' }});
            const data = await resp.json();
            if (data.status === 'success') {{
              if (statusEl) statusEl.textContent = 'Auto-tuned weights successfully! Reloading... ✓';
              setTimeout(() => location.reload(), 1500);
            }} else {{
              if (statusEl) statusEl.textContent = data.message || 'No overrides found yet to tune weights.';
            }}
          }} catch(err) {{
            if (statusEl) statusEl.textContent = 'Error auto-tuning: ' + err;
          }}
        }}

        function openScoreBreakdown(emailId) {{
          const email = EMAILS.find(e => e.id === emailId);
          if (!email) return;
          const sb = email.score_breakdown || {{}};

          const modalBody = document.getElementById('modal-body-content');
          modalBody.innerHTML = `
            <div style="font-size: 14px; font-weight: 700; margin-bottom: 12px;">${{escapeHtml(email.subject)}}</div>
            <div class="breakdown-row"><span>Category</span><strong>${{email.category}}</strong></div>
            <div class="breakdown-row"><span>Category Weight</span><strong>${{sb.category_weight || 1.0}}</strong></div>
            <div class="breakdown-row"><span>Keyword Score</span><strong>+${{(sb.keyword_score || 0).toFixed(2)}}</strong></div>
            <div class="breakdown-row"><span>Deadline Proximity Boost</span><strong>+${{(sb.deadline_boost || 0).toFixed(2)}}</strong></div>
            <div class="breakdown-row" style="border-bottom: none; pt: 12px; font-size: 15px;">
              <span>Final Priority Score</span>
              <strong style="color: var(--accent-blue);">${{email.importance_score}}</strong>
            </div>
            <div style="margin-top: 14px; background: rgba(56, 189, 248, 0.1); padding: 10px; border-radius: 8px; font-size: 12px; color: var(--accent-blue);">
              Mapped to <strong>${{email.importance.toUpperCase()}}</strong> priority tier based on threshold rules.
            </div>
          `;
          document.getElementById('score-modal').style.display = 'flex';
        }}

        async function openGCal(id, dateIdx = 0) {{
          try {{
            const resp = await fetch(`/emails/${{id}}/gcal-link?date_idx=${{dateIdx}}`);
            const data = await resp.json();
            if (data.status === 'success' && data.gcal_url) {{
              window.open(data.gcal_url, '_blank');
            }}
          }} catch(err) {{
            alert('Failed to open Google Calendar link: ' + err);
          }}
        }}

        let activeDraftEmailId = null;

        function openDraftModal(emailId) {{
          activeDraftEmailId = emailId;
          document.getElementById('draft-modal').style.display = 'flex';
          generateReplyDraft('confirm');
        }}

        function closeDraftModal() {{
          document.getElementById('draft-modal').style.display = 'none';
        }}

        async function generateReplyDraft(intent) {{
          if (!activeDraftEmailId) return;
          const bodyInp = document.getElementById('draft-body-inp');
          const subjInp = document.getElementById('draft-subject-inp');
          if (bodyInp) bodyInp.value = 'Generating smart reply draft...';

          try {{
            const resp = await fetch(`/emails/${{activeDraftEmailId}}/draft-reply?intent=${{intent}}`, {{ method: 'POST' }});
            const data = await resp.json();
            if (data.status === 'success' && data.draft) {{
              if (subjInp) subjInp.value = data.draft.reply_subject || '';
              if (bodyInp) bodyInp.value = data.draft.reply_body || '';
            }}
          }} catch(err) {{
            if (bodyInp) bodyInp.value = 'Failed to generate draft: ' + err;
          }}
        }}

        function copyDraftToClipboard() {{
          const subj = document.getElementById('draft-subject-inp').value;
          const body = document.getElementById('draft-body-inp').value;
          const textToCopy = `Subject: ${{subj}}\n\n${{body}}`;
          navigator.clipboard.writeText(textToCopy).then(() => {{
            const statusEl = document.getElementById('copy-status');
            if (statusEl) statusEl.textContent = 'Copied to clipboard! ✓';
            setTimeout(() => {{ if (statusEl) statusEl.textContent = ''; }}, 2000);
          }});
        }}

        function closeScoreModal() {{
          document.getElementById('score-modal').style.display = 'none';
        }}

        async function summarizeEmail(id) {{
          const btn = window.event ? window.event.target : null;
          if (btn) btn.textContent = '⏳ Summarizing...';
          try {{
            const resp = await fetch(`/emails/${{id}}/summarize`, {{ method: 'POST' }});
            const data = await resp.json();
            if (data.status === 'success') {{
              const email = EMAILS.find(e => e.id === id);
              if (email) {{
                email.summary = data.summary;
                email.action_items = data.action_items;
              }}
              renderInbox();
            }}
          }} catch(err) {{
            alert('Failed to summarize email: ' + err);
          }} finally {{
            if (btn) btn.textContent = '🤖 AI Summarize';
          }}
        }}

        async function toggleRead(id, status) {{
          await fetch(`/emails/${{id}}/read?is_read=${{status}}`, {{ method: 'POST' }});
          const email = EMAILS.find(e => e.id === id);
          if (email) email.is_read = status;
          renderInbox();
        }}

        async function setOverride(id, tier) {{
          await fetch(`/emails/${{id}}/override`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ importance: tier }})
          }});
          location.reload();
        }}

        async function dismissReminder(id) {{
          await fetch(`/reminders/${{id}}/dismiss`, {{ method: 'POST' }});
          location.reload();
        }}

        async function deleteEmail(id) {{
          if (confirm("Are you sure you want to delete this email?")) {{
            await fetch(`/emails/${{id}}`, {{ method: 'DELETE' }});
            location.reload();
          }}
        }}

        async function syncGmail() {{
          const icon = document.getElementById('sync-icon');
          if (icon) {{
            icon.style.display = 'inline-block';
            icon.style.animation = 'spin 1s linear infinite';
          }}
          try {{
            await fetch('/api/sync', {{ method: 'POST' }});
          }} catch (err) {{
            console.error('Failed to sync Gmail:', err);
          }}
          location.reload();
        }}

        function escapeHtml(str) {{
          const div = document.createElement('div');
          div.textContent = str || '';
          return div.innerHTML;
        }}

        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', () => {{
            navigator.serviceWorker.register('/service-worker.js')
              .catch(err => console.error('SW registration failed:', err));
          }});
        }}

        let deferredPrompt;
        const installBtn = document.getElementById('install-btn');
        if (installBtn) {{
          window.addEventListener('beforeinstallprompt', (e) => {{
            e.preventDefault();
            deferredPrompt = e;
            installBtn.style.display = 'inline-flex';
          }});

          installBtn.addEventListener('click', async () => {{
            if (deferredPrompt) {{
              deferredPrompt.prompt();
              const {{ outcome }} = await deferredPrompt.userChoice;
              if (outcome === 'accepted') {{
                installBtn.style.display = 'none';
              }}
              deferredPrompt = null;
            }}
          }});
        }}

        renderInbox();
      </script>
    </body>
    </html>
    """


