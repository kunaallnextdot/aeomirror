"""Authentication primitives (Phase 5): password hashing, JWT access tokens,
opaque refresh/verification/reset/invite tokens, and password-strength checks.

Design notes:
- Passwords are hashed with bcrypt (per-password salt, cost 12). The plaintext is
  never stored or logged.
- Access tokens are short-lived signed JWTs (HS256) carrying the user id, org id
  and role. They are self-contained so protected endpoints need no DB lookup to
  authorize (a DB check still confirms the account is active).
- Refresh / verification / reset / invitation tokens are opaque high-entropy
  random strings. Only their sha256 hash is stored, so a database leak cannot be
  used to mint sessions or reset passwords. The raw token is shown to the user
  exactly once (cookie / email link).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt truncates silently at 72 bytes; reject longer inputs up front so a long
# password isn't quietly weakened.
_BCRYPT_MAX_BYTES = 72
_ACCESS_TOKEN_TYPE = "access"


# ------------------------------- passwords -------------------------------
def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError("Password is too long (max 72 bytes).")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# Minimum policy: >= 8 chars, at least one letter and one digit. Returned message
# is safe to show the user.
_PW_MIN = 8


def password_problem(password: str) -> str | None:
    """Return a human-readable reason the password is too weak, or None if OK."""
    if len(password) < _PW_MIN:
        return f"Password must be at least {_PW_MIN} characters."
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        return "Password is too long (max 72 bytes)."
    if not re.search(r"[A-Za-z]", password):
        return "Password must contain at least one letter."
    if not re.search(r"\d", password):
        return "Password must contain at least one number."
    return None


# ------------------------------- opaque tokens -------------------------------
def generate_token() -> str:
    """A high-entropy URL-safe token (refresh / verify / reset / invite)."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """sha256 of an opaque token — what we persist. Constant-time comparison is
    achieved by looking the row up by this hash (an index lookup, no plaintext)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------- JWT access tokens -------------------------------
def create_access_token(*, user_id: str, org_id: str | None, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org": org_id,
        "role": role,
        "type": _ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """Return the claims of a valid, unexpired access token, else None. Never raises."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    if claims.get("type") != _ACCESS_TOKEN_TYPE or not claims.get("sub"):
        return None
    return claims


# ------------------------------- misc helpers -------------------------------
# DB-stored datetimes are naive UTC, matching the rest of the schema (created_at
# uses datetime.utcnow) and how SQLite/psycopg return them. Compare with utcnow().
def now_utc() -> datetime:
    return datetime.utcnow()


def token_expiry(seconds: int) -> datetime:
    return datetime.utcnow() + timedelta(seconds=seconds)


def refresh_expiry(remember: bool) -> datetime:
    ttl = (settings.refresh_token_remember_seconds if remember
           else settings.refresh_token_ttl_seconds)
    return token_expiry(ttl)


def slugify(name: str) -> str:
    """Lowercase, hyphenated slug from an org name. Falls back to a random suffix
    so a slug is always producible; uniqueness is enforced by the caller."""
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return base or f"org-{secrets.token_hex(3)}"
