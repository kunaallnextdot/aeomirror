"""HTTP-route-level tests for the four new/extended GA4 Website Intelligence
endpoints (/overview, /trends, /channels, /landing-pages) in
app/api/routes_integrations.py. Mirrors test_integrations_properties.py's exact
conventions (auth_client, _enable_ga4, cross-tenant isolation pattern) — that file
covers /status, /properties, /select-property, /sync, /traffic, /disconnect; this
one covers the newer endpoints that file predates.

Regression context: these endpoints returned a bare 500 in a real local dev
environment because migration b4f7e1a9c2d3 existed in code but was never applied
to the running database — every query against GoogleAnalyticsConnection or the new
Ga4* tables raised sqlalchemy.exc.OperationalError ("no such column"/"no such
table"). The test suite's own create_all()-based DB setup (conftest.py) cannot
reproduce that specific failure mode (it always creates a schema matching the
current models), so this file cannot and does not re-create that exact bug — see
test_migration_schema_sync.py for the migration/model-drift check that can. What
this file DOES verify: once the schema is correct, every endpoint behaves
correctly end to end — success, Pro-gating, missing/revoked credentials, and
cross-tenant isolation — through the real HTTP layer, not just the service
functions underneath (which test_ga4_website_intelligence.py already covers)."""
from datetime import date, datetime, timedelta

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, Ga4LandingPageSnapshot, Ga4TrafficSnapshot,
    GoogleAnalyticsConnection, Monitor, Scan,
)
from app.db.session import SessionLocal
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="
_TODAY_KEY = date.today().strftime("%Y%m%d")


def _enable_ga4(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "csecret")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/integrations/google/callback")
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)


def _make_connection(org_id: str, user_id: str, *, status: str = GA4_STATUS_ACTIVE,
                     property_id: str | None = "123456") -> str:
    db = SessionLocal()
    try:
        conn = GoogleAnalyticsConnection(
            organization_id=org_id, connected_by_user_id=user_id,
            encrypted_refresh_token=token_crypto.encrypt_token("rt-secret"),
            status=status, ga4_property_id=property_id,
            connected_at=datetime.utcnow(), last_synced_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(conn)
        db.commit()
        db.refresh(conn)
        return conn.id
    finally:
        db.close()


def _seed_traffic(connection_id: str, org_id: str):
    db = SessionLocal()
    try:
        db.add(Ga4TrafficSnapshot(
            organization_id=org_id, connection_id=connection_id, date=_TODAY_KEY,
            channel_grouping="Referral", source="chatgpt.com", medium="referral",
            ai_source_label="chatgpt", sessions=10, new_users=4, engaged_sessions=6,
            views=20, engagement_duration_sec=90.0, key_events=1,
        ))
        db.commit()
    finally:
        db.close()


def _seed_landing_page(connection_id: str, org_id: str):
    db = SessionLocal()
    try:
        db.add(Ga4LandingPageSnapshot(
            organization_id=org_id, connection_id=connection_id, date=_TODAY_KEY,
            landing_page="/pricing", sessions=50, new_users=20, engaged_sessions=10,
            views=60, engagement_duration_sec=40.0, key_events=0,
        ))
        db.commit()
    finally:
        db.close()


def _patch_refresh_ok(monkeypatch):
    async def fake_refresh(refresh_token):
        return {"access_token": "at-fresh"}
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)


def _patch_refresh_revoked(monkeypatch):
    async def fake_refresh(refresh_token):
        return None
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)


# ------------------------------- /overview -------------------------------

def test_overview_success_returns_current_and_previous_period(monkeypatch):
    _enable_ga4(monkeypatch)
    _patch_refresh_ok(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])

    async def fake_overview(token, property_id, *, current_start, current_end, previous_start, previous_end):
        assert property_id == "123456"
        return {"current": {"total_users": 10, "new_users": 4, "sessions": 20, "engaged_sessions": 12,
                            "views": 40, "engagement_duration_sec": 100.0, "key_events": 2},
                "previous": {"total_users": 8, "new_users": 3, "sessions": 15, "engaged_sessions": 9,
                             "views": 30, "engagement_duration_sec": 80.0, "key_events": 1}}
    monkeypatch.setattr(ga4, "fetch_overview_with_comparison", fake_overview)

    r = client.get("/integrations/google/overview")
    assert r.status_code == 200
    body_json = r.json()
    assert body_json["current"]["total_users"] == 10
    assert body_json["current"]["returning_users"] == 6   # derived: 10 - 4
    assert body_json["previous"]["total_users"] == 8


def test_overview_401_when_credential_is_revoked(monkeypatch):
    _enable_ga4(monkeypatch)
    _patch_refresh_revoked(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])

    r = client.get("/integrations/google/overview")
    assert r.status_code == 401


def test_overview_404_without_a_connection(monkeypatch):
    _enable_ga4(monkeypatch)
    client, _ = auth_client()
    r = client.get("/integrations/google/overview")
    assert r.status_code == 404


def test_overview_402_without_pro(monkeypatch):
    _enable_ga4(monkeypatch)
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])
    r = client.get("/integrations/google/overview")
    assert r.status_code == 402


# ------------------------------- /trends -------------------------------

def test_trends_reads_stored_snapshots_and_merges_ai_sessions_by_day(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    conn_id = _make_connection(body["organization"]["id"], body["user"]["id"])
    _seed_traffic(conn_id, body["organization"]["id"])

    db = SessionLocal()
    try:
        from app.db.models import Ga4DailyOverview
        db.add(Ga4DailyOverview(
            organization_id=body["organization"]["id"], connection_id=conn_id, date=_TODAY_KEY,
            total_users=7, new_users=4, sessions=10, engaged_sessions=6, views=20,
            engagement_duration_sec=90.0, key_events=1,
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/integrations/google/trends")
    assert r.status_code == 200
    daily = r.json()["daily"]
    assert len(daily) == 1
    assert daily[0]["total_users"] == 7
    assert daily[0]["ai_sessions"]["chatgpt"] == 10


def test_trends_empty_when_never_synced_is_not_an_error(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    _make_connection(body["organization"]["id"], body["user"]["id"])
    r = client.get("/integrations/google/trends")
    assert r.status_code == 200
    assert r.json()["daily"] == []


# ------------------------------- /channels -------------------------------

def test_channels_sort_search_and_pagination(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    conn_id = _make_connection(body["organization"]["id"], body["user"]["id"])
    _seed_traffic(conn_id, body["organization"]["id"])

    r = client.get("/integrations/google/channels", params={"q": "chatgpt"})
    assert r.status_code == 200
    rows = r.json()["channels"]
    assert len(rows) == 1
    assert rows[0]["source"] == "chatgpt.com"
    assert rows[0]["engagement_rate"] == 0.6   # 6 engaged / 10 sessions

    r_miss = client.get("/integrations/google/channels", params={"q": "nonexistent-source"})
    assert r_miss.json()["channels"] == []


def test_channels_cross_tenant_isolation(monkeypatch):
    _enable_ga4(monkeypatch)
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    conn_b = _make_connection(body_b["organization"]["id"], body_b["user"]["id"])
    _seed_traffic(conn_b, body_b["organization"]["id"])

    assert client_a.get("/integrations/google/channels").status_code == 404
    r_b = client_b.get("/integrations/google/channels")
    assert r_b.status_code == 200
    assert len(r_b.json()["channels"]) == 1


# ------------------------------- /landing-pages -------------------------------

def test_landing_pages_success_and_scan_cross_reference(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    conn_id = _make_connection(org_id, body["user"]["id"])
    _seed_landing_page(conn_id, org_id)

    db = SessionLocal()
    try:
        monitor = Monitor(organization_id=org_id, user_id=body["user"]["id"],
                          url="https://acme.example.com", normalized_url="acme.example.com")
        db.add(monitor)
        db.commit()
        scan = Scan(url="https://acme.example.com/pricing",
                   normalized_url="https://acme.example.com/pricing",
                   ars=82, rubric_version="v1", result={}, organization_id=org_id,
                   created_at=datetime.utcnow())
        db.add(scan)
        db.commit()
    finally:
        db.close()

    r = client.get("/integrations/google/landing-pages")
    assert r.status_code == 200
    pages = r.json()["pages"]
    assert len(pages) == 1
    assert pages[0]["landing_page"] == "/pricing"
    assert pages[0]["scan"] is not None
    assert pages[0]["scan"]["score"] == 82


def test_landing_pages_no_scan_match_returns_none_not_an_error(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    conn_id = _make_connection(body["organization"]["id"], body["user"]["id"])
    _seed_landing_page(conn_id, body["organization"]["id"])

    r = client.get("/integrations/google/landing-pages")
    assert r.status_code == 200
    assert r.json()["pages"][0]["scan"] is None


def test_landing_pages_cross_tenant_isolation(monkeypatch):
    _enable_ga4(monkeypatch)
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    conn_b = _make_connection(body_b["organization"]["id"], body_b["user"]["id"])
    _seed_landing_page(conn_b, body_b["organization"]["id"])

    assert client_a.get("/integrations/google/landing-pages").status_code == 404
    assert len(client_b.get("/integrations/google/landing-pages").json()["pages"]) == 1


# ------------------------------- per-dataset freshness + comparability + mapping -------------------------------

def _set_conn(conn_id: str, **values):
    db = SessionLocal()
    try:
        db.query(GoogleAnalyticsConnection).filter_by(id=conn_id).update(values)
        db.commit()
    finally:
        db.close()


def test_trends_and_landing_pages_report_their_own_dataset_sync_not_the_global_one(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    conn_id = _make_connection(body["organization"]["id"], body["user"]["id"])   # traffic synced
    t = client.get("/integrations/google/trends").json()
    lp = client.get("/integrations/google/landing-pages").json()
    assert t["synced"] is False and t["last_synced_at"] is None
    assert lp["synced"] is False and lp["last_synced_at"] is None
    _set_conn(conn_id, overview_synced_at=datetime.utcnow())
    assert client.get("/integrations/google/trends").json()["synced"] is True
    assert client.get("/integrations/google/status").json()["datasets"]["landing_pages"] is None


def test_landing_page_change_is_withheld_when_history_does_not_cover_the_previous_period(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id = body["organization"]["id"]
    conn_id = _make_connection(org_id, body["user"]["id"])
    _seed_landing_page(conn_id, org_id)          # only today's row: no previous-period history
    r = client.get("/integrations/google/landing-pages").json()
    assert r["previous_comparable"] is False
    assert r["pages"][0]["sessions_change"] is None   # unknown, never "+50 vs 0"

    db = SessionLocal()
    try:
        old = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
        db.add(Ga4LandingPageSnapshot(organization_id=org_id, connection_id=conn_id, date=old,
                                      landing_page="/other", sessions=1, new_users=1,
                                      engaged_sessions=1, views=1, engagement_duration_sec=1.0,
                                      key_events=0))
        db.commit()
    finally:
        db.close()
    r = client.get("/integrations/google/landing-pages").json()
    assert r["previous_comparable"] is True
    pricing = next(p for p in r["pages"] if p["landing_page"] == "/pricing")
    assert pricing["sessions_change"] is None   # absent in previous → still not invented


def test_landing_page_audit_match_uses_the_explicitly_linked_website(monkeypatch):
    _enable_ga4(monkeypatch)
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    conn_id = _make_connection(org_id, user_id)
    _seed_landing_page(conn_id, org_id)
    db = SessionLocal()
    try:
        a = Monitor(organization_id=org_id, user_id=user_id, url="https://a.example", normalized_url="a.example")
        b = Monitor(organization_id=org_id, user_id=user_id, url="https://b.example", normalized_url="b.example")
        db.add_all([a, b]); db.commit()
        db.add(Scan(url="https://b.example/pricing", normalized_url="b.example/pricing", ars=61,
                    rubric_version="v1", result={}, organization_id=org_id, created_at=datetime.utcnow()))
        db.commit()
        b_id = b.id
    finally:
        db.close()
    # Two websites, no link: ambiguous, so no audit is attributed.
    assert client.get("/integrations/google/landing-pages").json()["pages"][0]["scan"] is None
    _set_conn(conn_id, linked_monitor_id=b_id)
    scan = client.get("/integrations/google/landing-pages").json()["pages"][0]["scan"]
    assert scan is not None and scan["score"] == 61
