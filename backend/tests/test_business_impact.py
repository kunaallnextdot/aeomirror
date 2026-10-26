"""AI Business Impact: aggregation logic (app/services/analytics/aggregation.py) and
the dashboard API (app/api/routes_business_impact.py) — date filtering, revenue only
counting verified conversions, landing-page prioritization/insufficient-data
labeling, Pro gating, and multi-tenant isolation."""
from datetime import datetime, timedelta

from app.billing import entitlements
from app.config import settings
from app.db.models import SUB_CANCELED, AnalyticsConversion, AnalyticsSession, Monitor, Subscription
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


def test_summary_counts_page_views_and_unique_visitors():
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        s1 = AnalyticsSession(organization_id=org_id, monitor_id=monitor_id, session_id="s1",
                              visitor_id="v1", channel="chatgpt", landing_url="https://example.com/",
                              started_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), page_view_count=3)
        s2 = AnalyticsSession(organization_id=org_id, monitor_id=monitor_id, session_id="s2",
                              visitor_id="v1", channel="direct", landing_url="https://example.com/",
                              started_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), page_view_count=2)
        # No visitor_id at all (storage unavailable) — still a real session, but never
        # counted toward the unique-visitor estimate.
        s3 = AnalyticsSession(organization_id=org_id, monitor_id=monitor_id, session_id="s3",
                              visitor_id=None, channel="direct", landing_url="https://example.com/",
                              started_at=datetime.utcnow(), last_seen_at=datetime.utcnow(), page_view_count=1)
        db.add_all([s1, s2, s3])
        db.commit()

        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        result = aggregation.summary(db, org_id=org_id, monitor_ids=None, start=start, end=end)
        assert result["total_sessions"] == 3
        assert result["total_page_views"] == 6          # 3 + 2 + 1
        assert result["unique_visitors"] == 1            # v1 across 2 sessions, s3 has no visitor_id
        assert result["ai_unique_visitors"] == 1          # v1's chatgpt session
    finally:
        db.close()


def test_summary_excludes_a_diagnostic_test_visit_from_real_traffic_counts():
    # A manual verification visit (landing_url carrying the reserved
    # "aeomirror_diagnostic" marker — see ANALYTICS_TRACKING.md) must never be
    # mistaken for real customer traffic in any metric.
    client, body = auth_client()
    org_id, user_id = body["organization"]["id"], body["user"]["id"]
    monitor_id = _make_monitor(org_id, user_id)
    db = SessionLocal()
    try:
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="direct",
                    landing_url="https://example.com/?aeomirror_diagnostic=1")
        _add_session(db, org_id=org_id, monitor_id=monitor_id, channel="chatgpt",
                    landing_url="https://example.com/pricing")   # a real visit
        db.commit()

        start, end = datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)
        result = aggregation.summary(db, org_id=org_id, monitor_ids=None, start=start, end=end)
        assert result["total_sessions"] == 1
        assert result["ai_sessions"] == 1

        pages = aggregation.landing_page_performance(db, org_id=org_id, monitor_ids=None,
                                                      start=start, end=end, include_aeo=False)
        assert all("aeomirror_diagnostic" not in p["url"] for p in pages)
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

def test_summary_unlocked_for_a_real_active_29_dollar_pro_subscription(monkeypatch):
    # The $29/month plan IS the Pro plan (settings.pro_price_cents == 2900) — there
    # is no separate tier. Goes through the REAL checkout + webhook-completion flow
    # (same as test_billing.py), not a hand-crafted DB row, so this proves the
    # actual entitlement path an upgrade takes, not just the plan-code comparison.
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, _ = auth_client()
    assert settings.pro_price_cents == 2900
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})

    r = client.get("/business-impact/summary")
    data = r.json()
    assert data["is_pro"] is True
    assert data["ai_conversions"] is not None
    assert data["ai_revenue_cents"] is not None
    assert data["confirmed_bookings"] is not None
    assert data["leads"] is not None


def test_ai_conversion_rate_is_not_locked_for_a_real_pro_account_with_zero_ai_sessions(monkeypatch):
    # The actual regression this ticket reports: ai_conversion_rate_pct is a RATIO
    # that the aggregation layer also returns null for whenever there are zero
    # AI-referred sessions (division by zero avoided, not a plan restriction) —
    # `is_pro` must stay true, and the OTHER Pro-only fields must stay real ints
    # (never re-nulled), so the frontend can tell "no data" apart from "not Pro".
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    ref = client.post("/billing/checkout", json={"plan_code": "pro"}).json()["reference"]
    client.post("/billing/checkout/complete", json={"reference": ref})
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        _add_session(db, org_id=body["organization"]["id"], monitor_id=monitor_id, channel="direct")
        db.commit()
    finally:
        db.close()

    r = client.get("/business-impact/summary")
    data = r.json()
    assert data["is_pro"] is True
    assert data["ai_conversion_rate_pct"] is None   # 0 AI sessions -> no ratio, not 0%
    assert data["ai_conversions"] == 0               # a real zero, never re-locked to null
    assert data["confirmed_bookings"] == 0


def test_summary_stays_locked_for_a_cancelled_subscription(monkeypatch):
    # A CANCELED (not just "cancel_at_period_end") subscription must never grant
    # Pro access — current_plan() only honors status == SUB_ACTIVE.
    monkeypatch.setattr(settings, "billing_enforced", True)
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        db.add(Subscription(organization_id=body["organization"]["id"], plan_code="pro",
                            status=SUB_CANCELED, provider="stripe",
                            provider_subscription_id="sub_cancelled_1"))
        db.commit()
    finally:
        db.close()
    r = client.get("/business-impact/summary")
    data = r.json()
    assert data["is_pro"] is False
    assert data["ai_conversions"] is None


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


def _preset_range(days):
    """Exactly what the frontend sends for a "Last N days" button (BusinessImpact.jsx's
    isoDaysAgo/todayIso): today's date, and today's date minus N days — both plain
    calendar dates, no time component."""
    today = datetime.utcnow().date()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def test_business_impact_last_7_30_and_90_day_presets_are_all_accepted():
    # The exact regression: "Last 90 days" spans TODAY plus the 90 days before it —
    # 91 calendar days inclusive — and previously 422'd with "cannot exceed 90 days"
    # even though this preset IS the one the dashboard itself offers.
    client, _ = auth_client()
    for days in (7, 30, 90):
        start_date, end_date = _preset_range(days)
        r = client.get("/business-impact/summary", params={"start_date": start_date, "end_date": end_date})
        assert r.status_code == 200, f"Last {days} days preset: {r.status_code} {r.text}"


def test_business_impact_91_calendar_days_still_rejected_beyond_the_90_day_preset():
    # The fix widens tolerance by exactly the one day the inclusive-end-date bump
    # needs — it must not silently accept an actually-longer range (e.g. a 92-day
    # span, one day past the "Last 90 days" preset's own 91-day span).
    client, _ = auth_client()
    today = datetime.utcnow().date()
    start_date = (today - timedelta(days=91)).isoformat()   # 92 calendar days, start..today inclusive
    r = client.get("/business-impact/summary", params={"start_date": start_date, "end_date": today.isoformat()})
    assert r.status_code == 422


def test_business_impact_90_day_preset_across_a_year_boundary():
    client, _ = auth_client()
    r = client.get("/business-impact/summary",
                   params={"start_date": "2025-11-01", "end_date": "2026-01-30"})   # 91 days, crosses New Year
    assert r.status_code == 200


def test_business_impact_90_day_preset_across_a_leap_year_february():
    client, _ = auth_client()
    # end_date minus exactly 90 days — the same construction as the "Last 90 days"
    # preset — landing on a range that includes the leap day, Feb 29 2024.
    r = client.get("/business-impact/summary",
                   params={"start_date": "2024-01-02", "end_date": "2024-04-01"})
    assert r.status_code == 200


def test_business_impact_cannot_be_scoped_to_another_orgs_monitor():
    client_a, _ = auth_client()
    client_b, body_b = auth_client()
    monitor_b = _make_monitor(body_b["organization"]["id"], body_b["user"]["id"])
    r = client_a.get("/business-impact/summary", params={"monitor_id": monitor_b})
    assert r.status_code == 404
