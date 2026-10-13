"""Authenticated symmetric encryption (Fernet: AES-128-CBC + HMAC-SHA256) for the ONE
secret this app persists that isn't already a hash/JWT: a connected organization's
Google OAuth refresh token (see app/services/integrations/google_analytics.py). No
home-grown cipher — Fernet is the standard, audited choice; the stdlib has no
symmetric cipher of its own.

Keyed by settings.token_encryption_key (validated at startup in app/config.py
whenever the GA4 integration is enabled — see ga4_integration_enabled / the
_validate_production model_validator). Every function here returns None rather than
raising on a decryption failure, matching this codebase's established "never crash
the caller on a credential problem" convention (see app/core/ai.py), and never logs
or returns the key, the plaintext token, or the ciphertext.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

log = logging.getLogger("app.token_crypto")


def _fernet() -> Fernet | None:
    key = settings.token_encryption_key
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:  # noqa: BLE001 — a malformed key must never crash the caller
        log.error("TOKEN_ENCRYPTION_KEY is configured but invalid")
        return None


def encrypt_token(plaintext: str) -> str | None:
    """Encrypt a refresh token for storage. Returns None (never raises) if encryption
    is unavailable (no/invalid key) — callers must treat that as "cannot persist this
    credential" rather than silently storing plaintext."""
    if not plaintext:
        return None
    fernet = _fernet()
    if fernet is None:
        return None
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str | None) -> str | None:
    """Decrypt a stored refresh token, or None on ANY failure (wrong/rotated key,
    corrupted ciphertext, missing key, empty input) — never raises, never logs the
    plaintext or ciphertext."""
    if not ciphertext:
        return None
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        log.warning("token decryption failed (invalid ciphertext or key mismatch)")
        return None
