"""Password hashing (PBKDF2-HMAC-SHA256, stdlib only) and JWT auth.

Input sanitization utilities for security hardening.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db

PBKDF2_ITERATIONS = int(os.environ.get("PASSWORD_HASH_ITERATIONS", "390000"))
_SCHEME = "pbkdf2_sha256"

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "{}${}${}${}".format(
        _SCHEME,
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = encoded.split("$")
        if scheme != _SCHEME:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# -------------------------------------------------------------------- JWT
def create_access_token(user_id: int, role: str) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + settings.jwt_expires_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ------------------------------------------------------- current user deps
def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
):
    """Resolve the authenticated user or raise 401."""
    from app.models.user import User

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    return user


def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
):
    """Like ``get_current_user`` but returns ``None`` when unauthenticated."""
    if credentials is None:
        return None
    try:
        return get_current_user(credentials, db)
    except HTTPException:
        return None


def require_admin(user=Depends(get_current_user)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]
OptionalUser = Annotated[dict | None, Depends(get_optional_user)]
DBSession = Annotated[Session, Depends(get_db)]


# ---------------------------------------------- input sanitization (Rule: strip HTML)


def strip_html(text: str | None) -> str | None:
    """Remove HTML tags from user input to prevent XSS (security hardening).

    This strips all HTML/XML tags from strings to prevent stored XSS attacks
    when user-supplied text is rendered in HTML contexts.
    """
    if text is None:
        return None
    # Remove HTML tags using a simple regex pattern
    # This handles most common cases including <script>, <img>, <a> tags
    clean_text = re.sub(r'<[^>]*>', '', text)
    # Remove HTML entities as well
    clean_text = clean_text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return clean_text.strip()


def sanitize_user_input(data: dict | str) -> dict | str:
    """Recursively sanitize all string values in a dictionary or clean a single string.

    This ensures that any user-supplied text fields are safe from HTML injection
    before they are stored in the database or rendered in responses.
    """
    if isinstance(data, dict):
        return {key: sanitize_user_input(value) for key, value in data.items()}
    elif isinstance(data, str):
        return strip_html(data)
    elif isinstance(data, list):
        return [sanitize_user_input(item) for item in data]
    else:
        return data
