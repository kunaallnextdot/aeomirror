"""AI Business Impact: aggregation logic (app/services/analytics/aggregation.py) and
the dashboard API (app/api/routes_business_impact.py) — date filtering, revenue only
counting verified conversions, landing-page prioritization/insufficient-data
labeling, Pro gating, and multi-tenant isolation."""
from datetime import datetime, timedelta

from app.billing import entitlements
from app.config import settings
from app.db.models import AnalyticsConversion, AnalyticsSession, Monitor
from app.db.session import SessionLocal
from app.services.analytics import aggregation
from tests.authutil import auth_client


def _make_monitor(org_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url="https://example.com/",
                    normalized_url="example.com", frequency="manual", status="active")
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def _add_session(db, *, org_id, monitor_id, channel, landing_url="https://example.com/",
                 started_at=None, session_id=None):
    now = started_at or datetime.utcnow()
    s = AnalyticsSession(organization_id=org_id, monitor_id=monitor_id,
                         session_id=session_id or f"s-{datetime.utcnow().timestamp()}-{channel}",
                         landing_url=landing_url, channel=channel,
                         started_at=now, last_seen_at=now, page_view_count=1)
    db.add(s); db.flush()
    return s


def _add_conversion(db, *, org_id, monitor_id, session_id=None, event_type="purchase",
                    external_id, value_cents=None, verified=True, created_at=None):
    c = AnalyticsConversion(organization_id=org_id, monitor_id=monitor_id,
                            analytics_session_id=session_id, event_type=event_type,
                            external_id=external_id, value_cents=value_cents,
                            currency="USD" if value_cents else None, verified=verified,
                            created_at=created_at or datetime.utcnow(),
                            updated_at=created_at or datetime.utcnow())
    db.add(c)
    return c


# ------------------------------- aggregation.summary -------------------------------

def test_summary_counts_ai_share_and_only_verified_revenue():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        s_ai = _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="chatgpt")
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="direct")
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="organic_search")
        db.flush()
        # Verified purchase attributed to the AI session.
        _add_conversion(db, org_id=org_id, monitor_id=monitor_id, session_id=s_ai.id,
                        external_id="o1", value_cents=5000, verified=True)
        # Unverified (client-reported only) purchase — must NOT count toward revenue.
        _add_conversion(db, org_id=org_id, monitor_id=monitor_id, session_id=s_ai.id,
                        external_id="o2", value_cents=9999, verified=False)
        db.commit()

        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        result = aggregation.summary(db, org_id=org_id, monitor_ids=None, start=start, end=end)
        assert result["total_sessions"] == 3
        assert result["ai_sessions"] == 1
        assert round(result["ai_share_pct"]) == 33
        assert result["ai_conversions"] == 1
        assert result["ai_revenue_cents"] == 5000   # unverified o2 excluded
    finally:
        db.close()


def test_summary_separates_unverified_from_confirmed_leads_and_bookings():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="direct")
        db.flush()
        _add_conversion(db, org_id=org_id, monitor_id=monitor_id, event_type="lead_submitted",
                        external_id="lead-confirmed", verified=True)
        _add_conversion(db, org_id=org_id, monitor_id=monitor_id, event_type="lead_submitted",
                        external_id="lead-unverified", verified=False)
        _add_conversion(db, org_id=org_id, monitor_id=monitor_id, event_type="booking_confirmed",
                        external_id="booking-unverified", verified=False)
        db.commit()

        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        result = aggregation.summary(db, org_id=org_id, monitor_ids=None, start=start, end=end)
        # Confirmed counts never include the unverified rows...
        assert result["leads"] == 1
        assert result["confirmed_bookings"] == 0
        # ...and the unverified counts are a SEPARATE, explicit field, never silently
        # dropped or blended into the confirmed numbers above.
        assert result["unverified_leads"] == 1
        assert result["unverified_bookings"] == 1
    finally:
        db.close()


def test_summary_date_range_excludes_sessions_outside_it():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="direct",
                    started_at=datetime.utcnow() - timedelta(days=60))
        db.commit()
        start, end = datetime.utcnow() - timedelta(days=7), datetime.utcnow()
        result = aggregation.summary(db, org_id=org_id, monitor_ids=None, start=start, end=end)
        assert result["total_sessions"] == 0
        assert result["has_data"] is False
    finally:
        db.close()


def test_summary_is_isolated_per_organization():
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    monitor_a = _make_monitor(body_a["organization"]["id"], body_a["user"]["id"])
    monitor_b = _make_monitor(body_b["organization"]["id"], body_b["user"]["id"])
    db = SessionLocal()
    try:
        _add_session(db, org_id=body_a["organization"]["id"], monitor_id=monitor_a, channel="chatgpt")
        _add_session(db, org_id=body_b["organization"]["id"], monitor_id=monitor_b, channel="chatgpt")
        _add_session(db, org_id=body_b["organization"]["id"], monitor_id=monitor_b, channel="direct")
        db.commit()
        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        result_a = aggregation.summary(db, org_id=body_a["organization"]["id"], monitor_ids=None,
                                       start=start, end=end)
        assert result_a["total_sessions"] == 1   # never sees org B's 2 sessions
    finally:
        db.close()


# ------------------------------- landing-page prioritization -------------------------------

def test_landing_page_insufficient_data_label_below_sample_threshold():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        for i in range(3):   # well under aggregation._MIN_SAMPLE_SESSIONS
            _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="direct",
                        landing_url="https://example.com/new-page")
        db.commit()
        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        pages = aggregation.landing_page_performance(db, org_id=org_id, monitor_ids=None,
                                                      start=start, end=end, include_aeo=False)
        assert pages[0]["confidence"] == "insufficient_data"
        assert pages[0]["priority"] == "unknown"
    finally:
        db.close()


def test_landing_page_high_ai_traffic_low_conversion_flagged_high_priority():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        for i in range(30):   # 30 AI sessions, 0 conversions -> high ai share, low conv rate
            _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="chatgpt",
                        landing_url="https://example.com/struggling-page")
        db.commit()
        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        pages = aggregation.landing_page_performance(db, org_id=org_id, monitor_ids=None,
                                                      start=start, end=end, include_aeo=False)
        page = pages[0]
        assert page["pattern"] == "high_ai_traffic_low_conversion"
        assert page["priority"] == "high"
        assert "reason" in page and page["reason"]   # explainable, not a black box
    finally:
        db.close()


def test_landing_page_never_estimates_a_revenue_uplift():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        for i in range(25):
            _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="chatgpt",
                        landing_url="https://example.com/p")
        db.commit()
        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        pages = aggregation.landing_page_performance(db, org_id=org_id, monitor_ids=None,
                                                      start=start, end=end, include_aeo=False)
        for p in pages:
            assert "estimated_revenue" not in p
            assert "projected_revenue" not in p
    finally:
        db.close()


# ------------------------------- route-level: Pro gating + isolation -------------------------------

def test_summary_endpoint_nulls_conversion_fields_on_free_plan(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    r = client.get("/business-impact/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["is_pro"] is False
    assert data["ai_revenue_cents"] is None
    assert data["ai_conversions"] is None
    assert data["confirmed_bookings"] is None
    assert data["unverified_bookings"] is None
    assert data["leads"] is None
    assert data["unverified_leads"] is None
    # But the traffic-share numbers ARE visible on free (explicit product requirement).
    assert "total_sessions" in data
    assert "ai_share_pct" in data


def test_channels_and_landing_pages_require_pro(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    assert client.get("/business-impact/channels").status_code == 402
    assert client.get("/business-impact/landing-pages").status_code == 402


def test_business_impact_date_validation():
    client, _ = auth_client()
    assert client.get("/business-impact/summary",
                      params={"start_date": "not-a-date"}).status_code == 422
    assert client.get("/business-impact/summary",
                      params={"start_date": "2026-01-01", "end_date": "2025-01-01"}).status_code == 422
    assert client.get("/business-impact/summary",
                      params={"start_date": "2020-01-01", "end_date": "2026-01-01"}).status_code == 422


def test_business_impact_cannot_be_scoped_to_another_orgs_monitor():
    client_a, _ = auth_client()
    client_b, body_b = auth_client()
    monitor_b = _make_monitor(body_b["organization"]["id"], body_b["user"]["id"])
    r = client_a.get("/business-impact/summary", params={"monitor_id": monitor_b})
    assert r.status_code == 404
