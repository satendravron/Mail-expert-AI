"""
Data models for Mail Expert AI.
Uses Pydantic so these double as request/response schemas for the API layer.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class Importance(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    PLACEMENT = "placement"
    INDUSTRY = "industry"
    CLUB = "club"
    EVENT = "event"
    UNCATEGORIZED = "uncategorized"


class SenderAction(str, Enum):
    ALWAYS_HIGH = "always_high"
    ALWAYS_LOW = "always_low"
    MUTE = "mute"
    NONE = "none"


class ExtractedDate(BaseModel):
    label: str                     # e.g. "Application Deadline"
    datetime_utc: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    raw_text: str


class Email(BaseModel):
    id: str
    user_id: str
    source: str                    # gmail | outlook | imap | pasted
    sender: str
    subject: str
    body: str                      # full body available to the engine at score-time
    received_at: datetime

    # Fields populated BY the engine
    category: Category = Category.UNCATEGORIZED
    importance: Optional[Importance] = None
    importance_score: float = 0.0          # 0.0 - 1.0
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    extracted_dates: List[ExtractedDate] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    action_items: List[str] = Field(default_factory=list)
    user_override: Optional[Importance] = None
    is_read: bool = False
    account_label: str = "Primary Account"
    is_replied: bool = False
    reply_sent_at: Optional[datetime] = None


class SendReplyRequest(BaseModel):
    email_id: str
    recipient: str
    subject: str
    body: str
    intent: str = "confirm"
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None


class IncomingWebhookPayload(BaseModel):
    id: Optional[str] = None
    user_id: str = "local_user"
    sender: str
    subject: str
    body: str
    source: str = "webhook"
    account_label: str = "Primary Account"


class UserRegisterRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None


class UserLoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    full_name: Optional[str] = None


class AccountConfig(BaseModel):
    id: str
    user_id: str
    account_name: str
    provider: str                  # gmail | imap | outlook | mock
    email_address: str
    is_active: bool = True
    last_synced_at: Optional[datetime] = None


class SenderRule(BaseModel):
    sender: str                    # exact address OR "@domain.com"
    action: SenderAction = SenderAction.NONE


class Preferences(BaseModel):
    user_id: str
    timezone: str = "UTC"
    sender_rules: List[SenderRule] = Field(default_factory=list)
    category_weights: Dict[str, float] = Field(default_factory=lambda: {
        Category.PLACEMENT.value: 1.0,
        Category.INDUSTRY.value: 0.8,
        Category.CLUB.value: 0.4,
        Category.EVENT.value: 0.6,
        Category.UNCATEGORIZED.value: 0.3,
    })
    # thresholds are configurable per user so power users can tighten/loosen tiers
    high_threshold: float = 0.7
    medium_threshold: float = 0.4


class Reminder(BaseModel):
    id: str
    email_id: str
    user_id: str
    title: str
    due_at: datetime
    notify_offsets_minutes: List[int] = Field(default_factory=lambda: [24 * 60, 60])
    channels: List[str] = Field(default_factory=lambda: ["push", "in_app"])
    status: str = "pending"        # pending | fired | dismissed | snoozed
