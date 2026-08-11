"""
Extracts deadlines / event dates from email subject+body.

Strategy (deliberately simple + dependency-light so it runs anywhere):
 1. Look for phrases anchored to urgency/date keywords ("last date", "deadline",
    "by", "on or before", "within X hours/days") and try to parse a date from
    the text following/around them.
 2. Fall back to a fuzzy full-text scan with dateutil for any date-like string.
 3. Score each hit with a confidence based on how it was found.

This module has no external network calls and no LLM dependency, so it works
offline and cheaply at ingestion time. It can be swapped later for an
LLM-based extractor (see `extract_dates_llm` stub) without changing callers.
"""

from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

import os
from models import ExtractedDate

# Phrases that usually precede/anchor a real deadline (not just any date
# mentioned in an email, e.g. "founded in 1999" shouldn't count).
DEADLINE_ANCHORS = [
    r"last date(?:\s+to\s+apply)?",
    r"deadline",
    r"apply\s+by",
    r"submit(?:ted)?\s+by",
    r"due\s+(?:date|by|on)",
    r"closes?\s+on",
    r"on\s+or\s+before",
    r"registration\s+ends?",
    r"interview\s+(?:scheduled|on|confirm)?",
    r"final\s+call",
    r"confirm(?:\s+your)?\s+interview",
]

RELATIVE_PATTERNS = [
    (re.compile(r"within\s+(\d+)\s+hours?", re.I), lambda m, now: now + timedelta(hours=int(m.group(1)))),
    (re.compile(r"within\s+(\d+)\s+days?", re.I), lambda m, now: now + timedelta(days=int(m.group(1)))),
    (re.compile(r"\bin\s+(\d+)\s+hours?", re.I), lambda m, now: now + timedelta(hours=int(m.group(1)))),
    (re.compile(r"\bin\s+(\d+)\s+days?", re.I), lambda m, now: now + timedelta(days=int(m.group(1)))),
    (re.compile(r"\btoday\b", re.I), lambda m, now: now),
    (re.compile(r"\btomorrow\b", re.I), lambda m, now: now + timedelta(days=1)),
    (re.compile(r"\bnext week\b", re.I), lambda m, now: now + timedelta(weeks=1)),
    (re.compile(r"\bend of (?:the )?month\b", re.I), lambda m, now: now + relativedelta(day=31)),
]

ANCHOR_RE = re.compile(
    r"(?:" + "|".join(DEADLINE_ANCHORS) + r")\s*[:\-]?\s*(.{0,40})",
    re.IGNORECASE,
)

# A conservative window used to sanity-check absolute dates found in free text
# (rejects wildly out-of-range parses like phone numbers being read as dates).
MIN_YEAR_OFFSET = -1
MAX_YEAR_OFFSET = 3


def _safe_parse(text: str, now: datetime) -> Optional[datetime]:
    try:
        dt = dateparser.parse(text, fuzzy=True, default=now)
        if now.year + MIN_YEAR_OFFSET <= dt.year <= now.year + MAX_YEAR_OFFSET:
            return dt
    except (ValueError, OverflowError):
        return None
    return None


def extract_dates(subject: str, body: str, now: Optional[datetime] = None) -> List[ExtractedDate]:
    """
    Returns a de-duplicated, confidence-scored list of ExtractedDate objects
    found in the subject + body text.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    text = f"{subject}\n{body}"
    results: List[ExtractedDate] = []
    seen_dt = set()

    # 1. Direct relative time pattern matching across text (highest confidence for relative deadlines)
    for pattern, resolver in RELATIVE_PATTERNS:
        rel_match = pattern.search(text)
        if rel_match:
            parsed = resolver(rel_match, now)
            if parsed and parsed not in seen_dt:
                seen_dt.add(parsed)
                results.append(ExtractedDate(
                    label=_guess_label(rel_match.group(0)),
                    datetime_utc=parsed,
                    confidence=0.9,
                    raw_text=rel_match.group(0).strip(),
                ))

    # 2. Anchored absolute/relative dates
    for match in ANCHOR_RE.finditer(text):
        window = match.group(0)
        snippet = match.group(1).strip()

        parsed = None
        for pattern, resolver in RELATIVE_PATTERNS:
            rel_match = pattern.search(window)
            if rel_match:
                parsed = resolver(rel_match, now)
                break
        if parsed is None and snippet:
            parsed = _safe_parse(snippet, now)

        if parsed and parsed not in seen_dt:
            seen_dt.add(parsed)
            results.append(ExtractedDate(
                label=_guess_label(window),
                datetime_utc=parsed,
                confidence=0.9,
                raw_text=window.strip(),
            ))

    # 3. Fallback: any other date-like token in the text, lower confidence.
    relative_units_re = re.compile(r"\b\d+\s+(?:hours?|days?|weeks?|months?|years?)\b", re.I)
    for token in re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+\w+(?:\s+\d{2,4})?\b", text):
        if relative_units_re.search(token):
            continue  # relative durations handled in Step 1
        parsed = _safe_parse(token, now)
        if parsed and parsed not in seen_dt:
            seen_dt.add(parsed)
            results.append(ExtractedDate(
                label="Mentioned Date",
                confidence=0.5,
                datetime_utc=parsed,
                raw_text=token,
            ))

    results.sort(key=lambda d: d.datetime_utc)
    return results


def _guess_label(anchor_text: str) -> str:
    anchor_text = anchor_text.lower()
    if "interview" in anchor_text:
        return "Interview Date"
    if "registration" in anchor_text:
        return "Registration Deadline"
    if "last date" in anchor_text or "deadline" in anchor_text or "due" in anchor_text:
        return "Application Deadline"
    return "Important Date"


def extract_dates_llm(subject: str, body: str, now: Optional[datetime] = None) -> List[ExtractedDate]:
    """
    LLM-backed deadline extractor fallback.
    Uses GEMINI_API_KEY or OPENAI_API_KEY if available in environment.
    If no key or API call fails, falls back gracefully to extract_dates rule-engine.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return extract_dates(subject, body, now)

    try:
        # Fallback to local rule engine if no API provider library initialized
        return extract_dates(subject, body, now)
    except Exception:
        return extract_dates(subject, body, now)
