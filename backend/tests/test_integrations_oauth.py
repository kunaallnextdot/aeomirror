"""Google Analytics OAuth flow (app/api/routes_integrations.py's connect/callback +
app/services/integrations/google_analytics.py's state/exchange logic). All real
Google HTTP calls are monkeypatched at the module-function boundary (the same
pattern tests/test_answer_tracking.py uses for provider calls) — no real network.
"""
from datetime import datetime, timedelta

from app.config import settings
from app.db.models import GA4_STATUS_PENDING_PROPERTY, GoogleAnalyticsConnection, OAuthState
from app.db.session import SessionLocal
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="   # a real-looking Fernet key


def _enable_ga4(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/integrations/google/callback")
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)


def test_connect_disabled_without_configuration(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    client, _ = auth_client()
    r = client.get("/integrations/google/connect")
    assert r.status_code == 503
    # The detail must be a clear, specific "not configured" message — not a bare
    # 503 the frontend could only report as a vague "something went wrong" (see
    # api.ga4_errors.test.js's matching regression test on the frontend side).
    assert r.json()["detail"] == "Google Analytics integration is not configured on this server."


def test_connect_returns_authorize_url_bound_to_caller(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/connect")
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge=" in url and "code_challenge_method=S256" in url
    assert "state=" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fanalytics.readonly" in url

    db = SessionLocal()
    try:
        rows = db.query(OAuthState).all()
        assert len(rows) == 1
        assert rows[0].consumed is False
    finally:
        db.close()


def test_callback_rejects_invalid_state(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/callback", params={"code": "abc", "state": "not-a-real-state"},
                   follow_redirects=False)
    assert r.status_code == 302
    assert "ga4=error" in r.headers["location"]
    assert "reason=invalid_state" in r.headers["location"]


def test_callback_rejects_replayed_state(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=body["organization"]["id"], user_id=body["user"]["id"])
        state = state_row.state
    finally:
        db.close()

    async def fake_exchange(code, verifier):
        return {"access_token": "at-1", "refresh_token": "rt-1"}
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", fake_exchange)

    r1 = client.get("/integrations/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "ga4=connected" in r1.headers["location"]

    # Replaying the SAME state a second time must be rejected, not re-processed.
    r2 = client.get("/integrations/google/callback", params={"code": "c2", "state": state}, follow_redirects=False)
    assert "ga4=error" in r2.headers["location"]
    assert "reason=invalid_state" in r2.headers["location"]


def test_callback_rejects_expired_state(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=body["organization"]["id"], user_id=body["user"]["id"])
        state_row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        state = state_row.state
    finally:
        db.close()

    r = client.get("/integrations/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "ga4=error" in r.headers["location"]
    assert "reason=invalid_state" in r.headers["location"]


def test_callback_handles_user_cancellation(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/callback",
                   params={"error": "access_denied"}, follow_redirects=False)
    assert "ga4=error" in r.headers["location"]
    assert "reason=access_denied" in r.headers["location"]


def test_callback_handles_token_exchange_failure(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=body["organization"]["id"], user_id=body["user"]["id"])
        state = state_row.state
    finally:
        db.close()

    async def fake_exchange(code, verifier):
        return None   # transport/HTTP failure
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", fake_exchange)

    r = client.get("/integrations/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "ga4=error" in r.headers["location"]
    assert "reason=token_exchange_failed" in r.headers["location"]


def test_callback_without_refresh_token_on_first_connect_fails(monkeypatch):
    """Google only returns refresh_token with prompt=consent/offline access — a first
    connect that somehow gets none has nothing persistable."""
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=org_id, user_id=body["user"]["id"])
        state = state_row.state
    finally:
        db.close()

    async def fake_exchange(code, verifier):
        return {"access_token": "at-1"}   # no refresh_token
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", fake_exchange)

    r = client.get("/integrations/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "reason=missing_refresh_token" in r.headers["location"]

    db = SessionLocal()
    try:
        assert db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).count() == 0
    finally:
        db.close()


def test_successful_connect_persists_encrypted_connection_pending_property(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=org_id, user_id=body["user"]["id"])
        state = state_row.state
    finally:
        db.close()

    async def fake_exchange(code, verifier):
        assert verifier   # the PKCE verifier was threaded through
        return {"access_token": "at-1", "refresh_token": "rt-super-secret-value"}
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", fake_exchange)

    r = client.get("/integrations/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "ga4=connected" in r.headers["location"]

    db = SessionLocal()
    try:
        conn = db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).first()
        assert conn is not None
        assert conn.status == GA4_STATUS_PENDING_PROPERTY   # no property selected yet
        assert conn.encrypted_refresh_token is not None
        assert "rt-super-secret-value" not in conn.encrypted_refresh_token
    finally:
        db.close()

    status = client.get("/integrations/google/status").json()
    assert status["status"] == "pending_property"
    # The status response must never leak the token/ciphertext.
    assert "refresh_token" not in status and "encrypted_refresh_token" not in status


def test_reconnect_does_not_overwrite_valid_refresh_token_with_empty_one(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]

    async def first_exchange(code, verifier):
        return {"access_token": "at-1", "refresh_token": "rt-original"}
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", first_exchange)
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=org_id, user_id=body["user"]["id"])
        state1 = state_row.state
    finally:
        db.close()
    client.get("/integrations/google/callback", params={"code": "c1", "state": state1}, follow_redirects=False)

    db = SessionLocal()
    try:
        original_ciphertext = db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).first().encrypted_refresh_token
    finally:
        db.close()

    # Reconnect — Google returns no refresh_token this time (common on a re-consent).
    async def second_exchange(code, verifier):
        return {"access_token": "at-2"}   # no refresh_token
    monkeypatch.setattr(ga4, "exchange_code_for_tokens", second_exchange)
    db = SessionLocal()
    try:
        state_row, _ = ga4.create_oauth_state(db, org_id=org_id, user_id=body["user"]["id"])
        state2 = state_row.state
    finally:
        db.close()
    r = client.get("/integrations/google/callback", params={"code": "c2", "state": state2}, follow_redirects=False)
    assert "ga4=connected" in r.headers["location"]

    db = SessionLocal()
    try:
        conn = db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).first()
        assert conn.encrypted_refresh_token == original_ciphertext   # untouched
    finally:
        db.close()
