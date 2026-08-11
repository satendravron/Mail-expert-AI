"""
Calendar Processor for Mail Expert AI.

Features:
 1. Standard iCalendar (.ics) file content generator (RFC 5545 compliant) for Apple Calendar, Outlook, Thunderbird, etc.
 2. Google Calendar 1-Click Web URL builder.
 3. Google Calendar REST API event creation (if token.json OAuth authorized).
"""

from __future__ import annotations
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from models import Email, ExtractedDate


def format_ics_datetime(dt: datetime) -> str:
    """Formats datetime object into UTC ISO string required by RFC 5545 (YYYYMMDDTHHMMSSZ)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")


def generate_ics_content(title: str, start_dt: datetime, end_dt: Optional[datetime] = None, description: str = "") -> str:
    """
    Generates standard RFC 5545 iCalendar (.ics) string.
    """
    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)

    now_str = format_ics_datetime(datetime.now(timezone.utc))
    start_str = format_ics_datetime(start_dt)
    end_str = format_ics_datetime(end_dt)

    # Sanitize text fields
    clean_title = title.replace("\n", " ").replace("\r", "")
    clean_desc = description.replace("\n", "\\n").replace("\r", "")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Mail Expert AI//Calendar Processor//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:mailexpert-{now_str}-{hash(title) & 0xffffffff}@local",
        f"DTSTAMP:{now_str}",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{clean_title}",
        f"DESCRIPTION:{clean_desc}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR"
    ]
    return "\r\n".join(lines)


def generate_gcal_web_link(title: str, start_dt: datetime, end_dt: Optional[datetime] = None, description: str = "") -> str:
    """
    Generates a 1-click Google Calendar web creation URL.
    """
    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)

    start_str = format_ics_datetime(start_dt)
    end_str = format_ics_datetime(end_dt)

    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{start_str}/{end_str}",
        "details": description,
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"


def export_email_deadline(email_dict: Dict[str, Any], date_idx: int = 0) -> Dict[str, Any]:
    """
    Given an email dictionary from db, returns .ics content and gcal web link for the specified extracted date.
    """
    dates = email_dict.get("extracted_dates", [])
    subject = email_dict.get("subject", "Deadline")
    sender = email_dict.get("sender", "")
    body = email_dict.get("body", "")

    if not dates:
        dt = datetime.now(timezone.utc) + timedelta(days=1)
        label = "Deadline"
    else:
        d = dates[min(date_idx, len(dates) - 1)]
        raw_dt = d.get("datetime_utc")
        if isinstance(raw_dt, str):
            dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
        elif isinstance(raw_dt, datetime):
            dt = raw_dt
        else:
            dt = datetime.now(timezone.utc)
        label = d.get("label", "Deadline")

    title = f"[{label}] {subject}"
    desc = f"Source Email: {subject}\nFrom: {sender}\n\nSummary/Body:\n{email_dict.get('summary') or body[:250]}"

    ics = generate_ics_content(title=title, start_dt=dt, description=desc)
    gcal_url = generate_gcal_web_link(title=title, start_dt=dt, description=desc)

    return {
        "title": title,
        "due_at": dt.isoformat(),
        "ics_content": ics,
        "gcal_url": gcal_url
    }
