"""Token encryption (app/core/token_crypto.py) — the only secret this app persists
that isn't already a hash/JWT: a connected org's Google OAuth refresh token. Every
failure path must return None, never raise, and never leak the key/plaintext."""
from cryptography.fernet import Fernet

from app.config import settings
from app.core import token_crypto


def test_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    ciphertext = token_crypto.encrypt_token("ya29.refresh-token-secret")
    assert ciphertext is not None
    assert "ya29.refresh-token-secret" not in ciphertext   # not just base64 of the plaintext
    assert token_crypto.decrypt_token(ciphertext) == "ya29.refresh-token-secret"


def test_decrypt_with_wrong_key_fails_safely(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    ciphertext = token_crypto.encrypt_token("secret")
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    assert token_crypto.decrypt_token(ciphertext) is None


def test_decrypt_invalid_ciphertext_fails_safely(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    assert token_crypto.decrypt_token("not-a-real-fernet-token") is None
    assert token_crypto.decrypt_token("") is None
    assert token_crypto.decrypt_token(None) is None


def test_encrypt_without_key_configured_returns_none_not_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", "")
    assert token_crypto.encrypt_token("secret") is None
    assert token_crypto.decrypt_token("anything") is None


def test_malformed_key_fails_safely_rather_than_raising(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", "not-a-valid-fernet-key")
    assert token_crypto.encrypt_token("secret") is None


def test_empty_plaintext_is_not_encrypted(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    assert token_crypto.encrypt_token("") is None
    assert token_crypto.encrypt_token(None) is None
