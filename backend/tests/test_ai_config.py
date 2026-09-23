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


def test_content_insight_timeout_budget_is_internally_coherent():
    """Regression guard for a real production incident: content_insight_ai_max_tokens
    was raised (to fix a truncated-JSON bug) without content_insight_ai_timeout_seconds
    being raised to match. A real, content-rich page's single AI attempt was measured
    at 19.19s against the OLD 20s per-attempt timeout — a razor-thin ~0.8s margin that
    production's normal network/latency variance tipped over, triggering a retry that
    then also ran long and blew the overall budget, surfacing as an honest-but-wrong
    504 for a request that would have succeeded given a moment more. This pins the
    invariant that must hold so the same class of regression can't silently reappear
    if either value is tuned again later without checking the other."""
    from app.config import settings
    # A normal page re-fetch (no retries on THIS interactive path) plus ONE AI attempt
    # must fit comfortably inside the overall wall-clock budget, with real headroom
    # left for JSON parsing / threadpool dispatch / response transfer.
    single_attempt_worst_case = settings.fetch_timeout_seconds + settings.content_insight_ai_timeout_seconds
    assert single_attempt_worst_case < settings.content_insight_budget_seconds
    assert settings.content_insight_budget_seconds - single_attempt_worst_case >= 5
    # content_insight_ai_max_tokens raises the ceiling on how much the model can
    # generate for this prompt — the interactive per-attempt timeout must have
    # headroom to match, not stay sized for the smaller shared ai_max_tokens default.
    assert settings.content_insight_ai_max_tokens > settings.ai_max_tokens
