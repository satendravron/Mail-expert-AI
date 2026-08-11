"""
Auto-Draft & Smart Reply Generator for Mail Expert AI.

Features:
 1. Context-aware email response generation (Confirm Interview, Request Extension, Accept Offer, Decline, Clarify).
 2. Dual-mode engine: Online Gemini/OpenAI API or Offline Professional Template Generator.
"""

from __future__ import annotations
import os
import re
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

OFFLINE_TEMPLATES = {
    "confirm": (
        "Re: {subject}",
        "Dear {sender_name},\n\nThank you for the update. I confirm my availability and attendance for the scheduled slot/deadline mentioned.\n\nPlease let me know if any further details or documents are required.\n\nBest regards,\n[Your Name]"
    ),
    "extension": (
        "Re: {subject} — Extension Request",
        "Dear {sender_name},\n\nThank you for sending over the details. I am actively working on this, but due to prior academic/work commitments, I kindly request a short extension of 24–48 hours to complete and submit my response.\n\nThank you for your understanding.\n\nBest regards,\n[Your Name]"
    ),
    "accept": (
        "Re: {subject} — Acceptance Confirmation",
        "Dear {sender_name},\n\nThank you for this opportunity! I am delighted to accept and look forward to the next steps.\n\nPlease let me know if there are any onboarding forms or next steps I should complete.\n\nBest regards,\n[Your Name]"
    ),
    "decline": (
        "Re: {subject} — Response",
        "Dear {sender_name},\n\nThank you for considering me. Unfortunately, I am unable to proceed with this opportunity at this time due to scheduling conflicts.\n\nI appreciate your time and consideration.\n\nBest regards,\n[Your Name]"
    ),
    "clarification": (
        "Re: {subject} — Quick Query",
        "Dear {sender_name},\n\nThank you for your email. Could you please clarify the specific requirements or deadline details mentioned?\n\nLooking forward to your guidance.\n\nBest regards,\n[Your Name]"
    )
}


def _extract_sender_name(sender: str) -> str:
    if "<" in sender:
        name = sender.split("<")[0].strip().replace('"', '')
        if name:
            return name
    return sender.split("@")[0].capitalize()


def generate_offline_draft(subject: str, sender: str, intent: str = "confirm") -> Dict[str, str]:
    sender_name = _extract_sender_name(sender)
    clean_subj = re.sub(r"^(Re|Fwd):\s*", "", subject, flags=re.I)
    
    template_pair = OFFLINE_TEMPLATES.get(intent.lower(), OFFLINE_TEMPLATES["confirm"])
    reply_subject = template_pair[0].format(subject=clean_subj)
    reply_body = template_pair[1].format(sender_name=sender_name)

    return {
        "reply_subject": reply_subject,
        "reply_body": reply_body,
        "intent": intent
    }


def generate_reply_draft(subject: str, body: str, sender: str, intent: str = "confirm") -> Dict[str, str]:
    """
    Generates a smart response draft for an email based on user intent.
    Uses Gemini API if available, otherwise falls back to offline templates.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""
            Generate a professional email reply draft with intent '{intent}'.
            Return JSON with keys 'reply_subject' and 'reply_body'.

            Received Email Subject: {subject}
            From: {sender}
            Body: {body[:500]}
            """
            resp = model.generate_content(prompt)
            json_match = re.search(r"\{.*\}", resp.text.strip(), re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return {
                    "reply_subject": data.get("reply_subject", f"Re: {subject}"),
                    "reply_body": data.get("reply_body", ""),
                    "intent": intent
                }
        except Exception as e:
            logger.warning("Gemini API call for draft failed, falling back: %s", e)

    return generate_offline_draft(subject, sender, intent)
