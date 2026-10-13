"""Property listing/selection + status/traffic/disconnect endpoints and cross-tenant
isolation (app/api/routes_integrations.py). All Google HTTP calls monkeypatched."""
from datetime import datetime

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, GA4_STATUS_PENDING_PROPERTY, GA4_STATUS_REVOKED,
    Ga4TrafficSnapshot, GoogleAnalyticsConnection,
)
from app.db.session import SessionLocal
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="

PROPS = [
    {"property_id": "123456", "property_name": "Acme Main Site", "account_name": "Acme"},
    {"property_id": "789012", "property_name": "Acme Blog", "account_name": "Acme"},
]


def _enable_ga4(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/integrations/google/callback")
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)


def _make_connection(org_id: str, user_id: str, *, status: str = GA4_STATUS_PENDING_PROPERTY) -> str:
    db = SessionLocal()
    try:
        conn = GoogleAnalyticsConnection(
            organization_id=org_id, connected_by_user_id=user_id,
            encrypted_refresh_token=token_crypto.encrypt_token("rt-secret"),
            status=status, connected_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return conn.id
    finally:
        db.close()


def _patch_refresh_ok(monkeypatch):
    async def fake_refresh(refresh_token):
        return {"access_token": "at-fresh", "expires_in": 3600}
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)


# ------------------------------- properties -------------------------------

def test_properties_requires_a_connection(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/properties")
    assert r.status_code == 404


def test_properties_lists_accessible_properties(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_list(token):
        assert token == "at-fresh"
        return PROPS
    monkeypatch.setattr(ga4, "list_ga4_properties", fake_list)

    r = client.get("/integrations/google/properties")
    assert r.status_code == 200
    assert r.json()["properties"] == PROPS


def test_properties_empty_list_is_not_an_error(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)
    monkeypatch.setattr(ga4, "list_ga4_properties", lambda token: _async([]))

    r = client.get("/integrations/google/properties")
    assert r.status_code == 200
    assert r.json()["properties"] == []


def test_properties_credential_failure_returns_safe_502(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])

    async def fake_refresh(refresh_token):
        return None   # revoked / invalid
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)

    r = client.get("/integrations/google/properties")
    assert r.status_code == 502
    assert "token" not in r.json()["detail"].lower() or "rt-secret" not in r.text


async def _async(value):
    return value


# ------------------------------- select-property -------------------------------

def test_select_property_persists_only_after_validating_against_accessible_list(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    _make_connection(org_id, body["user"]["id"])
    _patch_refresh_ok(monkeypatch)
    monkeypatch.setattr(ga4, "list_ga4_properties", lambda token: _async(PROPS))

    r = client.post("/integrations/google/select-property", json={"property_id": "123456"})
    assert r.status_code == 200, r.text
    assert r.json()["ga4_property_id"] == "123456"

    db = SessionLocal()
    try:
        conn = db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).first()
        assert conn.status == GA4_STATUS_ACTIVE
        assert conn.ga4_property_name == "Acme Main Site"
        assert conn.next_sync_at is not None
    finally:
        db.close()


def test_select_property_rejects_an_inaccessible_or_arbitrary_id(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)
    monkeypatch.setattr(ga4, "list_ga4_properties", lambda token: _async(PROPS))

    r = client.post("/integrations/google/select-property", json={"property_id": "not-a-real-property"})
    assert r.status_code == 403


def test_select_property_without_connection_is_404(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.post("/integrations/google/select-property", json={"property_id": "123456"})
    assert r.status_code == 404


# ------------------------------- status/traffic -------------------------------

def test_status_not_connected_by_default(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/status")
    assert r.json() == {"status": "not_connected"}


def test_status_not_configured_when_platform_has_no_ga4_setup(monkeypatch):
    """When the operator hasn't set GOOGLE_OAUTH_*, /status must say so honestly —
    NOT "not_connected" (which would show a Connect button that 503s on click) and
    NOT "upgrade_required" for a non-Pro org (upgrading wouldn't fix anything)."""
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "")
    client, _ = auth_client()
    r = client.get("/integrations/google/status")
    assert r.json() == {"status": "not_configured"}


def test_status_not_configured_takes_priority_over_non_pro_org(monkeypatch):
    """A non-Pro org must ALSO see 'not_configured' (not 'upgrade_required') when the
    platform itself isn't set up — upgrading the org's plan can't fix a missing
    platform-level OAuth client, so the reason shown must be the real one."""
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, _ = auth_client()
    r = client.get("/integrations/google/status")
    assert r.json() == {"status": "not_configured"}


def test_traffic_distinguishes_never_synced_from_zero_sessions(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    conn_id = _make_connection(org_id, body["user"]["id"], status=GA4_STATUS_ACTIVE)
    db = SessionLocal()
    try:
        db.query(GoogleAnalyticsConnection).filter_by(id=conn_id).update({"ga4_property_id": "123456"})
        db.commit()
    finally:
        db.close()

    r = client.get("/integrations/google/traffic")
    assert r.status_code == 200
    body_json = r.json()
    assert body_json["synced"] is False
    assert body_json["last_synced_at"] is None
    assert all(v == 0 for v in body_json["by_source"].values())


def test_traffic_validates_date_range(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"], status=GA4_STATUS_ACTIVE)

    assert client.get("/integrations/google/traffic", params={"start_date": "not-a-date"}).status_code == 422
    assert client.get("/integrations/google/traffic",
                      params={"start_date": "2026-01-01", "end_date": "2025-01-01"}).status_code == 422
    assert client.get("/integrations/google/traffic",
                      params={"start_date": "2020-01-01", "end_date": "2026-01-01"}).status_code == 422


def test_traffic_aggregates_by_ai_source_label(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    conn_id = _make_connection(org_id, body["user"]["id"], status=GA4_STATUS_ACTIVE)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.add(Ga4TrafficSnapshot(organization_id=org_id, connection_id=conn_id, date="20261001",
                                  channel_grouping="Organic Search", source="google", medium="organic",
                                  ai_source_label="unattributable", sessions=40,
                                  created_at=now, updated_at=now))
        db.add(Ga4TrafficSnapshot(organization_id=org_id, connection_id=conn_id, date="20261001",
                                  channel_grouping="Referral", source="chatgpt.com", medium="referral",
                                  ai_source_label="chatgpt", sessions=12, created_at=now, updated_at=now))
        db.query(GoogleAnalyticsConnection).filter_by(id=conn_id).update(
            {"last_synced_at": now, "ga4_property_id": "123456"})
        db.commit()
    finally:
        db.close()

    r = client.get("/integrations/google/traffic",
                   params={"start_date": "2026-09-25", "end_date": "2026-10-05"})
    assert r.status_code == 200
    data = r.json()
    assert data["synced"] is True
    assert data["by_source"]["chatgpt"] == 12
    assert data["by_source"]["unattributable"] == 40
    assert data["by_source"]["gemini"] == 0


# ------------------------------- disconnect -------------------------------

def test_disconnect_invalidates_local_credential_even_if_remote_revoke_fails(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    _make_connection(org_id, body["user"]["id"], status=GA4_STATUS_ACTIVE)

    async def fake_revoke(token):
        return False   # Google unreachable / already revoked
    monkeypatch.setattr(ga4, "revoke_token", fake_revoke)

    r = client.delete("/integrations/google")
    assert r.status_code == 200

    db = SessionLocal()
    try:
        conn = db.query(GoogleAnalyticsConnection).filter_by(organization_id=org_id).first()
        assert conn.status == GA4_STATUS_REVOKED
        assert conn.encrypted_refresh_token is None
    finally:
        db.close()

    # The connection row is never deleted — a fresh page load (a NEW /status call,
    # not just the frontend's own optimistic post-disconnect state) must surface
    # "revoked" explicitly so the UI can render a distinct reconnect-required state,
    # rather than silently returning nothing the frontend recognizes.
    status_after = client.get("/integrations/google/status").json()
    assert status_after["status"] == "revoked"


# ------------------------------- tenant isolation -------------------------------

def test_unauthenticated_requests_are_rejected():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)   # no Authorization header attached
    assert client.get("/integrations/google/status").status_code == 401
    assert client.get("/integrations/google/connect").status_code == 401
    assert client.delete("/integrations/google").status_code == 401


def test_org_a_cannot_read_or_mutate_org_bs_connection(monkeypatch):
    _enable_ga4(monkeypatch)
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    org_b_id = body_b["organization"]["id"]
    _make_connection(org_b_id, body_b["user"]["id"], status=GA4_STATUS_ACTIVE)

    # Org A has no connection of its own — every endpoint must 404, not leak org B's.
    assert client_a.get("/integrations/google/properties").status_code == 404
    assert client_a.post("/integrations/google/select-property", json={"property_id": "123456"}).status_code == 404
    assert client_a.post("/integrations/google/sync").status_code == 404
    assert client_a.delete("/integrations/google").status_code == 404
    status_a = client_a.get("/integrations/google/status").json()
    assert status_a == {"status": "not_connected"}   # never org B's status

    traffic_a = client_a.get("/integrations/google/traffic")
    assert traffic_a.status_code == 404
