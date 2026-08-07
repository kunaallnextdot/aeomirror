"""AI config plumbing.

`settings.ai_enabled` must track whether ANTHROPIC_API_KEY is set, and the key value
must never leak into the admin health payload or the logs — only the boolean flag is
ever surfaced. This exercises config only; no AI feature code is called.
"""
import json
import logging

from app.admin import health
from app.config import settings
from app.db.session import SessionLocal


def test_ai_enabled_tracks_key_and_key_never_leaks(monkeypatch, caplog):
    sentinel = "sk-ant-SENTINEL-must-never-be-logged-0123456789"

    # Unset key -> AI disabled.
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    assert settings.ai_enabled is False

    # Key present -> AI enabled (no real key needed).
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "anthropic_api_key", sentinel)
    assert settings.ai_enabled is True

    # The admin health payload surfaces ONLY the boolean flag, never the key.
    db = SessionLocal()
    try:
        payload = health.system_health(db)
    finally:
        db.close()
    assert payload["ai_enabled"] is True
    assert sentinel not in json.dumps(payload, default=str)

    # Nothing we did emitted the key value to the logs.
    assert sentinel not in caplog.text
