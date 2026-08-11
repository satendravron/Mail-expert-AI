"""
LLM Processor for Mail Expert AI.

Features:
 1. Online AI Summarization & Action Item Extraction via Gemini/OpenAI API (if key configured).
 2. Rule-based Extractive Fallback Engine (offline/zero-dependency mode) that parses key
    action sentences and constructs executive summaries using NLP heuristic sentence filtering.
"""

from __future__ import annotations
import os
import re
import json
import logging
from typing import Dict, List, Any, Optional

from models import Email

logger = logging.getLogger(__name__)

ACTION_VERB_PATTERN = re.compile(
    r"\b(?:submit|apply|confirm|register|fill\s+out|upload|pay|complete|verify|reply|send|rsvp|attend|join|check|deadline|required|please)\b",
    re.IGNORECASE,
)


def _extractive_fallback(subject: str, body: str) -> Dict[str, Any]:
    """
    Fast, offline rule-based fallback processor that extracts key summary lines
    and identifies candidate action items using imperative verb matching.
    """
    clean_body = re.sub(r"\r\n|\r", "\n", body).strip()
    lines = [line.strip() for line in clean_body.split("\n") if line.strip()]

    # Break into sentences
    sentences = []
    for line in lines:
        for s in re.split(r"(?<=[.!?])\s+", line):
            s_clean = s.strip()
            if s_clean:
                sentences.append(s_clean)

    # Build Summary
    if sentences:
        first_meaningful = sentences[0]
        if len(first_meaningful) > 120:
            first_meaningful = first_meaningful[:117] + "..."
        summary = f"{subject}. {first_meaningful}"
    else:
        summary = subject

    # Extract Action Items
    action_items: List[str] = []
    seen = set()

    for sentence in sentences:
        if ACTION_VERB_PATTERN.search(sentence):
            # Clean up leading bullet points or symbols
            clean_item = re.sub(r"^[-*•\d+\.]+\s*", "", sentence).strip()
            if len(clean_item) > 10 and clean_item not in seen:
                seen.add(clean_item)
                action_items.append(clean_item)
                if len(action_items) >= 4:
                    break

    # Default action item if subject indicates action but none found in body
    if not action_items and ACTION_VERB_PATTERN.search(subject):
        action_items.append(subject)

    return {
        "summary": summary,
        "action_items": action_items,
    }


def generate_email_summary_and_actions(subject: str, body: str) -> Dict[str, Any]:
    """
    Generates summary and action items. Uses Gemini/OpenAI API if key present,
    otherwise uses the offline extractive fallback engine.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""
            Analyze the following email and return JSON with keys:
            - summary: a 1-2 sentence executive summary of the email.
            - action_items: a list of actionable tasks or deadlines for the recipient.

            Subject: {subject}
            Body: {body}
            """
            response = model.generate_content(prompt)
            # Try parsing JSON response
            text = response.text.strip()
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return {
                    "summary": data.get("summary", subject),
                    "action_items": data.get("action_items", []),
                }
        except Exception as e:
            logger.warning("Gemini API call failed, falling back to offline extractor: %s", e)

    elif openai_key:
        try:
            import urllib.request

            prompt_payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {
                        "role": "system",
                        "content": "You analyze emails and return JSON with 'summary' (str) and 'action_items' (list of str)."
                    },
                    {
                        "role": "user",
                        "content": f"Subject: {subject}\nBody: {body}"
                    }
                ],
                "response_format": {"type": "json_object"}
            }

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(prompt_payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {openai_key}"
                }
            )

            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"]
                data = json.loads(content)
                return {
                    "summary": data.get("summary", subject),
                    "action_items": data.get("action_items", []),
                }
        except Exception as e:
            logger.warning("OpenAI API call failed, falling back to offline extractor: %s", e)

    # Default: Extractive offline engine
    return _extractive_fallback(subject, body)


def enrich_email_with_llm(email: Email) -> Email:
    """
    Enriches an Email model instance with summary and action items.
    """
    result = generate_email_summary_and_actions(email.subject, email.body)
    email.summary = result.get("summary")
    email.action_items = result.get("action_items", [])
    return email
