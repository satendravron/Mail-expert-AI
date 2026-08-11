"""
Learning Processor for Mail Expert AI.

Self-Learning & Preference Auto-Tuning Engine:
 1. Analyzes user override logs (`override_logs`) to detect pattern biases.
 2. Calculates statistical weight adjustments per category.
 3. Auto-tunes user `Preferences.category_weights` and re-classifies emails so triage improves automatically over time.
"""

from __future__ import annotations
import db
from models import Preferences
from typing import Dict, Any


def compute_category_weight_deltas(user_id: str) -> Dict[str, float]:
    """
    Analyzes historical user override actions to calculate weight adjustment deltas
    per category.
    """
    logs = db.get_override_logs(user_id)
    if not logs:
        return {}

    category_deltas: Dict[str, float] = {}

    for log in logs:
        cat = log["category"].lower()
        orig = log.get("original_importance") or ""
        override = log.get("override_importance") or ""

        delta = 0.0
        if override == "high" and orig != "high":
            delta = +0.10
        elif override == "low" and orig != "low":
            delta = -0.10
        elif override == "medium":
            if orig == "low":
                delta = +0.05
            elif orig == "high":
                delta = -0.05

        category_deltas[cat] = category_deltas.get(cat, 0.0) + delta

    # Clamp deltas to reasonable bounds (-0.4 to +0.4)
    clamped_deltas = {}
    for cat, val in category_deltas.items():
        clamped_deltas[cat] = round(max(-0.4, min(0.4, val)), 2)

    return clamped_deltas


def auto_tune_user_preferences(user_id: str = "local_user") -> Dict[str, Any]:
    """
    Auto-tunes category weights in user preferences based on feedback history
    and updates SQLite.
    """
    prefs_dict = db.get_preferences_dict(user_id)
    deltas = compute_category_weight_deltas(user_id)

    if not deltas:
        return {
            "status": "no_overrides_found",
            "message": "No manual overrides recorded yet. Triage preferences unchanged.",
            "category_weights": prefs_dict.get("category_weights", {})
        }

    category_weights = prefs_dict.get("category_weights", {})
    adjustments_made = {}

    for cat, delta in deltas.items():
        current_val = category_weights.get(cat, 0.5)
        new_val = round(max(0.1, min(1.0, current_val + delta)), 2)
        if new_val != current_val:
            adjustments_made[cat] = {
                "old": current_val,
                "new": new_val,
                "delta": delta
            }
            category_weights[cat] = new_val

    prefs_dict["category_weights"] = category_weights
    db.save_preferences_dict(user_id, prefs_dict)

    # Optional: Re-classify existing emails using updated preferences
    raw_emails = db.get_all_emails(user_id)
    if raw_emails:
        import importance_engine
        from models import Email
        prefs_model = db.get_preferences_model(user_id)
        for raw in raw_emails:
            # Only re-classify emails that don't have a direct manual override
            if not raw.get("user_override"):
                try:
                    email_obj = Email(
                        id=raw["id"],
                        user_id=raw["user_id"],
                        source=raw["source"],
                        sender=raw["sender"],
                        subject=raw["subject"],
                        body=raw["body"],
                        received_at=raw["received_at"],
                    )
                    classified = importance_engine.classify_email(email_obj, prefs_model)
                    db.upsert_email(classified)
                except Exception:
                    pass

    return {
        "status": "success",
        "message": f"Auto-tuned category weights based on {len(deltas)} category feedback patterns.",
        "adjustments_made": adjustments_made,
        "category_weights": category_weights
    }
