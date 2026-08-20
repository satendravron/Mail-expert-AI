"""
Authentication & JWT Session Token Engine for Mail Expert AI.
"""

from __future__ import annotations
import os
import json
import base64
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import Request

SECRET_KEY = os.getenv("JWT_SECRET", "mail_expert_ai_jwt_secret_key_2026")
ALGORITHM = "HS256"
DEFAULT_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def _b64url_decode(str_val: str) -> bytes:
    padding = '=' * (4 - (len(str_val) % 4))
    return base64.urlsafe_b64decode((str_val + padding).encode('utf-8'))


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Generates an HMAC-SHA256 JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=DEFAULT_EXPIRE_MINUTES))
    to_encode["exp"] = int(expire.timestamp())

    header = {"alg": ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header).encode('utf-8'))
    payload_b64 = _b64url_encode(json.dumps(to_encode).encode('utf-8'))

    signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(SECRET_KEY.encode('utf-8'), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and verifies an HMAC-SHA256 JWT access token."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None

        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), signing_input, hashlib.sha256).digest()

        if not hmac.compare_digest(_b64url_encode(expected_sig), sig_b64):
            return None

        payload = json.loads(_b64url_decode(payload_b64).decode('utf-8'))
        exp = payload.get("exp")
        if exp and datetime.now(timezone.utc).timestamp() > exp:
            return None  # Token expired

        return payload
    except Exception:
        return None


def get_current_user_id(request: Request) -> str:
    """
    FastAPI dependency extracting active user_id from Authorization header or query param.
    Defaults to 'local_user' for seamless single-user / unauthenticated mode.
    """
    user_id = get_authenticated_user_id(request)
    if user_id:
        return user_id
    return request.query_params.get("user_id", "local_user")


def get_authenticated_user_id(request: Request) -> Optional[str]:
    """
    Extracts active user_id ONLY if a valid JWT token, cookie, or token parameter is present.
    Returns None if unauthenticated.
    """
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    elif "token" in request.query_params:
        token = request.query_params["token"]
    elif "session_token" in request.cookies:
        token = request.cookies["session_token"]
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]

    if token:
        payload = decode_access_token(token)
        if payload and "user_id" in payload:
            return payload["user_id"]

    return None
