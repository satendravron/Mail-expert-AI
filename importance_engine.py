"""
Importance Engine — decides HOW IMPORTANT an email is, using the
"instructions" embedded in the email itself (urgency language, deadlines,
action-required phrasing) combined with the user's own preferences
(pinned/muted senders, category weights).

Design: pure functions + one small class, no framework dependency, so this
can be unit-tested in isolation and reused by both the web-app backend and
the browser extension (via a thin API wrapper — see api.py).
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from models import Email, Preferences, SenderAction, Importance, Category
from date_extractor import extract_dates
from llm_processor import enrich_email_with_llm

# ---------------------------------------------------------------------------
# 1. Keyword libraries — the "instructions" the engine looks for inside the
#    mail text. Weights are additive signal strength (0-1 scale per hit,
#    capped later).
# ---------------------------------------------------------------------------

URGENCY_KEYWORDS: Dict[str, float] = {
    r"\burgent\b": 0.35,
    r"\bfinal\s+call\b": 0.35,
    r"\blast\s+date\b": 0.3,
    r"\bdeadline\b": 0.3,
    r"\bimmediate(?:ly)?\b": 0.25,
    r"\btoday\b": 0.2,
    r"\btomorrow\b": 0.15,
    r"\bwithin\s+24\s+hours\b": 0.3,
    r"\baction\s+required\b": 0.25,
    r"\bmandatory\b": 0.2,
    r"\bshortlisted\b": 0.25,
    r"\bselected\b": 0.25,
    r"\brejected\b": 0.15,
    r"\binterview\b": 0.25,
    r"\bconfirm(?:ation)?\s+required\b": 0.2,
}

LOW_SIGNAL_KEYWORDS: Dict[str, float] = {
    r"\bnewsletter\b": -0.25,
    r"\bunsubscribe\b": -0.15,
    r"\bfyi\b": -0.1,
    r"\bno\s+action\s+needed\b": -0.3,
    r"\breminder\s+only\b": -0.1,
}

CATEGORY_KEYWORDS: Dict[Category, List[str]] = {
    Category.PLACEMENT: [
        r"\bplacement\b", r"\brecruit", r"\bshortlist", r"\boffer\s+letter\b", r"\bcampus\s+drive\b",
        r"\bjob\s+alert\b", r"\bhiring\b", r"\bwe'?re\s+hiring\b", r"\bcareer\s+opportunit",
    ],
    Category.INDUSTRY: [
        r"\binternship\b", r"\bindustry\b", r"\bcorporate\b", r"\bhackathon\b",
        r"\bengineer\b", r"\bdeveloper\b", r"\bintern\s+at\b", r"\bapply\s+now\b",
        r"\bremote\b", r"\bposition\s+at\b", r"\brole\s+at\b",
    ],
    Category.CLUB: [r"\bclub\b", r"\bsociety\b", r"\bfest\b", r"\bcultural\b"],
    Category.EVENT: [r"\bworkshop\b", r"\bseminar\b", r"\bwebinar\b", r"\bevent\b", r"\bsession\b"],
}

# Baseline score for any email that's confidently in a real category (not
# UNCATEGORIZED). Prevents calm, low-urgency-language emails (a routine job
# alert, a placement office update) from scoring exactly 0 just because they
# don't contain words like "urgent" or "deadline" — they're still relevant,
# just not time-critical.
CATEGORY_BASELINE_SCORE = 0.15


def _keyword_score(text: str, table: Dict[str, float]) -> Tuple[float, List[str]]:
    """Sums weighted hits (each pattern counts once, capped) and reports which
    ones fired for the score_breakdown / explainability."""
    total = 0.0
    hits: List[str] = []
    for pattern, weight in table.items():
        if re.search(pattern, text, re.IGNORECASE):
            total += weight
            hits.append(pattern)
    return total, hits


def _detect_category(subject: str, body: str) -> Category:
    text = f"{subject}\n{body}"
    best_category = Category.UNCATEGORIZED
    best_hits = 0
    for category, patterns in CATEGORY_KEYWORDS.items():
        hits = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
        if hits > best_hits:
            best_hits = hits
            best_category = category
    return best_category


def _deadline_urgency_boost(email: Email, now: datetime) -> float:
    """
    The closer the nearest *credible, future* extracted deadline, the bigger
    the boost. Low-confidence fallback matches (confidence < 0.6, e.g. a
    stray date-like token) are ignored here so they can't mask a real
    deadline elsewhere in the same email — they still show up in
    extracted_dates for the user to see, just don't drive the score.
    """
    now_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    candidates = [
        d.datetime_utc.replace(tzinfo=timezone.utc) if d.datetime_utc.tzinfo is None else d.datetime_utc
        for d in email.extracted_dates
        if d.confidence >= 0.6
    ]
    future_candidates = [dt for dt in candidates if dt >= now_utc]
    if not future_candidates:
        return 0.0          # no credible upcoming deadline — don't inflate importance
    nearest = min(future_candidates)
    delta_hours = (nearest - now_utc).total_seconds() / 3600

    if delta_hours <= 24:
        return 0.35
    if delta_hours <= 72:
        return 0.2
    if delta_hours <= 24 * 7:
        return 0.1
    return 0.0


def _apply_sender_rule(email: Email, prefs: Preferences) -> SenderAction:
    for rule in prefs.sender_rules:
        if rule.sender.startswith("@"):
            if email.sender.lower().endswith(rule.sender.lower()):
                return rule.action
        elif rule.sender.lower() == email.sender.lower():
            return rule.action
    return SenderAction.NONE


def _tier_from_score(score: float, prefs: Preferences) -> Importance:
    if score >= prefs.high_threshold:
        return Importance.HIGH
    if score >= prefs.medium_threshold:
        return Importance.MEDIUM
    return Importance.LOW


def classify_email(email: Email, prefs: Preferences, now: datetime | None = None) -> Email:
    """
    Main entry point. Mutates and returns `email` with:
      - category
      - extracted_dates
      - importance_score (0-1) + score_breakdown (explainability)
      - importance tier (unless a hard sender rule overrides it)
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    text = f"{email.subject}\n{email.body}"

    # 0. Hard sender overrides short-circuit everything else.
    sender_action = _apply_sender_rule(email, prefs)
    if sender_action == SenderAction.MUTE:
        email.importance = Importance.LOW
        email.importance_score = 0.0
        email.score_breakdown = {"sender_rule": "muted"}
        email.category = _detect_category(email.subject, email.body)
        enrich_email_with_llm(email)
        return email
    if sender_action == SenderAction.ALWAYS_HIGH:
        email.importance = Importance.HIGH
        email.importance_score = 1.0
        email.score_breakdown = {"sender_rule": "always_high"}
        email.category = _detect_category(email.subject, email.body)
        email.extracted_dates = extract_dates(email.subject, email.body, now)
        enrich_email_with_llm(email)
        return email
    if sender_action == SenderAction.ALWAYS_LOW:
        email.importance = Importance.LOW
        email.importance_score = 0.1
        email.score_breakdown = {"sender_rule": "always_low"}
        email.category = _detect_category(email.subject, email.body)
        enrich_email_with_llm(email)
        return email

    # 1. Category detection (drives category_weights multiplier below).
    email.category = _detect_category(email.subject, email.body)

    # 2. Date extraction — needed both for score + downstream reminders.
    email.extracted_dates = extract_dates(email.subject, email.body, now)

    # 3. Keyword-based urgency/instruction signal.
    urgency_score, urgency_hits = _keyword_score(text, URGENCY_KEYWORDS)
    low_signal_score, low_hits = _keyword_score(text, LOW_SIGNAL_KEYWORDS)

    # 4. Deadline proximity boost.
    deadline_boost = _deadline_urgency_boost(email, now)

    # 5. Baseline relevance score just for being a real (non-uncategorized)
    #    category — see CATEGORY_BASELINE_SCORE comment above.
    baseline = CATEGORY_BASELINE_SCORE if email.category != Category.UNCATEGORIZED else 0.0

    # 6. Category weight multiplier (user preference).
    category_weight = prefs.category_weights.get(email.category.value, 0.5)

    raw_score = (urgency_score + low_signal_score + deadline_boost + baseline) * category_weight
    final_score = max(0.0, min(1.0, raw_score))

    email.importance_score = round(final_score, 3)
    email.score_breakdown = {
        "urgency_keywords": round(urgency_score, 3),
        "low_signal_keywords": round(low_signal_score, 3),
        "deadline_proximity": round(deadline_boost, 3),
        "category_baseline": baseline,
        "category_weight_multiplier": category_weight,
        "matched_urgency_patterns": urgency_hits,
        "matched_low_signal_patterns": low_hits,
    }
    email.importance = _tier_from_score(final_score, prefs)
    if not email.tags:
        email.tags = detect_tags(email.subject, email.body)
    enrich_email_with_llm(email)
    return email


TAG_PATTERNS: Dict[str, List[str]] = {
    "🏷️ Action Needed": [r"\burgent\b", r"\baction\s+required\b", r"\bplease\s+confirm\b", r"\bdeadline\b", r"\basap\b", r"\breview\s+needed\b"],
    "💼 Interview": [r"\binterview\b", r"\brecruiter\b", r"\bjob\s+offer\b", r"\bshortlisted?\b", r"\bapplication\b", r"\bhiring\b"],
    "💳 Financial": [r"\binvoice\b", r"\breceipt\b", r"\bpayment\b", r"\bsubscription\b", r"\bbilling\b", r"\btax\b"],
    "🚀 Project": [r"\brelease\b", r"\bsprint\b", r"\bmilestone\b", r"\bdeploy\b", r"\bbuild\s+update\b"],
    "⚠️ Security": [r"\bsecurity\s+alert\b", r"\bpassword\s+reset\b", r"\bunauthorized\b", r"\bverification\s+code\b", r"\blogin\s+attempt\b"]
}


def detect_tags(subject: str, body: str) -> List[str]:
    """Auto-detects semantic workflow tags from email subject and body text."""
    text = f"{subject} {body}".lower()
    tags = []
    for tag_name, patterns in TAG_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text):
                tags.append(tag_name)
                break
    return tags


def batch_classify(emails: List[Email], prefs: Preferences, now: datetime | None = None) -> List[Email]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return [classify_email(e, prefs, now) for e in emails]
