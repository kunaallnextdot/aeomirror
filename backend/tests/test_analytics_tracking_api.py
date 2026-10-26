"""Public tracking API (app/api/routes_tracking.py) — the collector, tracker.js, and
the authenticated setup + server-to-server confirm endpoints. Covers validation,
idempotency, rate limiting, multi-tenant isolation, and auth boundaries."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import AnalyticsConversion, AnalyticsSession, Monitor
from app.db.session import SessionLocal
from app.main import app
from tests.authutil import auth_client


def _make_monitor(org_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        m = Monitor(organization_id=org_id, user_id=user_id, url="https://example.com/",
                    normalized_url="example.com", frequency="manual", status="active")
        db.add(m)
        db.commit()
        db.refresh(m)
        return m.id
    finally:
        db.close()


def test_tracker_js_is_public_and_served_as_javascript():
    client = TestClient(app)
    r = client.get("/t/tracker.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "aeomirror" in r.text.lower()


def test_collect_unknown_site_id_returns_404_not_a_silent_write():
    client = TestClient(app)
    r = client.post("/t/collect", json={"site_id": "does-not-exist",
                                        "events": [{"event_id": "e1", "type": "pageview",
                                                   "session_id": "s1", "url": "https://x.com/"}]})
    assert r.status_code == 404


def test_collect_pageview_records_a_session_and_pageview():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    r = public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{
            "event_id": "evt-1", "type": "pageview", "session_id": "sess-1",
            "visitor_id": "vis-1", "url": "https://example.com/pricing",
            "referrer": "https://chat.openai.com/c/abc",
        }],
    })
    assert r.status_code == 204

    db = SessionLocal()
    try:
        sessions = db.query(AnalyticsSession).filter_by(monitor_id=monitor_id).all()
        assert len(sessions) == 1
        assert sessions[0].channel == "chatgpt"
        assert sessions[0].page_view_count == 1
    finally:
        db.close()


def test_collect_pageview_with_no_referrer_or_utm_is_accepted_as_direct():
    # "missing optional referrer" must never be treated as an error.
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    r = public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1",
                   "url": "https://example.com/"}],   # no referrer, no utm_*
    })
    assert r.status_code == 204
    db = SessionLocal()
    try:
        s = db.query(AnalyticsSession).filter_by(monitor_id=monitor_id).first()
        assert s is not None and s.channel == "direct"
    finally:
        db.close()


def test_collect_rejects_a_structurally_invalid_payload():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    # Missing the required `url` field entirely.
    r = public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1"}],
    })
    assert r.status_code == 422
    # An empty events list is also invalid (min_length=1).
    r2 = public.post("/t/collect", json={"site_id": monitor_id, "events": []})
    assert r2.status_code == 422


def test_collect_rejects_an_oversized_batch():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    events = [{"event_id": f"e{i}", "type": "pageview", "session_id": "s", "url": "https://x.com/"}
             for i in range(settings.tracking_max_events_per_batch + 1)]
    r = public.post("/t/collect", json={"site_id": monitor_id, "events": events})
    assert r.status_code == 422


def test_collect_conversion_requires_event_type_and_external_id_or_is_skipped():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    r = public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "conversion", "session_id": "s1",
                   "url": "https://example.com/", "event_type": "purchase"}],  # no external_id
    })
    assert r.status_code == 204   # accepted at the transport level, but...
    db = SessionLocal()
    try:
        assert db.query(AnalyticsConversion).filter_by(monitor_id=monitor_id).count() == 0
    finally:
        db.close()


def test_collect_conversion_is_never_verified_from_the_public_path():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "conversion", "session_id": "s1",
                   "url": "https://example.com/checkout/success",
                   "event_type": "purchase", "external_id": "order-1", "value_cents": 9900}],
    })
    db = SessionLocal()
    try:
        conv = db.query(AnalyticsConversion).filter_by(monitor_id=monitor_id, external_id="order-1").first()
        assert conv is not None
        assert conv.verified is False
    finally:
        db.close()


def test_collect_rejects_an_unrelated_origin_spoofing_another_sites_id():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    r = public.post("/t/collect", headers={"Origin": "https://attacker.example"}, json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1",
                   "url": "https://attacker.example/"}],
    })
    assert r.status_code == 403
    db = SessionLocal()
    try:
        assert db.query(AnalyticsSession).filter_by(monitor_id=monitor_id).count() == 0
    finally:
        db.close()


def test_collect_allows_the_monitors_own_origin_and_a_subdomain_of_it():
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])  # example.com
    public = TestClient(app)
    r1 = public.post("/t/collect", headers={"Origin": "https://example.com"}, json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1", "url": "https://example.com/"}],
    })
    r2 = public.post("/t/collect", headers={"Origin": "https://blog.example.com"}, json={
        "site_id": monitor_id,
        "events": [{"event_id": "e2", "type": "pageview", "session_id": "s2", "url": "https://blog.example.com/"}],
    })
    assert r1.status_code == 204
    assert r2.status_code == 204


def test_collect_accepts_the_real_registered_domain_end_to_end_via_post_monitors():
    # Registers the site through the SAME path a real customer uses (POST /monitors,
    # which stores normalized_url as a bare host via routes_scan._normalize — no
    # scheme, no "www." — not a URL urlparse can extract a hostname from), then
    # sends a collect event with the Origin a real browser on that exact site would
    # send. Closes the loop end-to-end rather than relying on the DB-insert test
    # helper's own (coincidentally identical) normalized_url format.
    client, body = auth_client()
    monitor_id = client.post("/monitors", json={"url": "https://www.realcustomer.example/",
                                                 "frequency": "manual"}).json()["id"]
    public = TestClient(app)
    r = public.post("/t/collect", headers={"Origin": "https://www.realcustomer.example"}, json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1",
                   "url": "https://www.realcustomer.example/"}],
    })
    assert r.status_code == 204
    db = SessionLocal()
    try:
        assert db.query(AnalyticsSession).filter_by(monitor_id=monitor_id).count() == 1
    finally:
        db.close()


def test_collect_allows_requests_with_no_origin_or_referer_at_all():
    # No signal to check against (e.g. a privacy-hardened browser) — fail open,
    # never break a legitimate install over a header it can't see.
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)
    r = public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1", "url": "https://example.com/"}],
    })
    assert r.status_code == 204


def test_collect_rate_limited_per_site_and_ip(monkeypatch):
    owner_client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    import app.api.routes_tracking as rt
    monkeypatch.setattr(rt, "tracking_limiter",
                        rt.RateLimiter(2, 60, prefix="aeomirror:rate-limit:tracking-test:"))
    public = TestClient(app)
    payload = {"site_id": monitor_id,
              "events": [{"event_id": "e", "type": "pageview", "session_id": "s", "url": "https://x.com/"}]}
    r1 = public.post("/t/collect", json={**payload, "events": [{**payload["events"][0], "event_id": "e1"}]})
    r2 = public.post("/t/collect", json={**payload, "events": [{**payload["events"][0], "event_id": "e2"}]})
    r3 = public.post("/t/collect", json={**payload, "events": [{**payload["events"][0], "event_id": "e3"}]})
    assert r1.status_code == 204 and r2.status_code == 204
    assert r3.status_code == 429


# ------------------------------- setup + tenant isolation -------------------------------

def test_tracking_status_reflects_actual_received_events_not_assumed_installation():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    r = client.get(f"/monitors/{monitor_id}/tracking")
    assert r.status_code == 200
    assert r.json()["installed"] is False
    assert r.json()["status"] == "not_installed"

    public = TestClient(app)
    public.post("/t/collect", json={
        "site_id": monitor_id,
        "events": [{"event_id": "e1", "type": "pageview", "session_id": "s1", "url": "https://example.com/"}],
    })
    r2 = client.get(f"/monitors/{monitor_id}/tracking")
    assert r2.json()["installed"] is True
    assert r2.json()["status"] == "active"


def test_tracking_status_flags_a_stale_install_as_inactive_not_an_error():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        stale = datetime.utcnow() - timedelta(days=30)
        db.add(AnalyticsSession(organization_id=body["organization"]["id"], monitor_id=monitor_id,
                                session_id="old-session", landing_url="https://example.com/",
                                channel="direct", started_at=stale, last_seen_at=stale, page_view_count=1))
        db.commit()
    finally:
        db.close()
    r = client.get(f"/monitors/{monitor_id}/tracking")
    data = r.json()
    assert data["installed"] is True      # a real event WAS recorded — never downgraded to False
    assert data["status"] == "installed_inactive"


def test_org_a_cannot_see_org_bs_monitor_tracking_status():
    client_a, _ = auth_client()
    client_b, body_b = auth_client()
    monitor_id = _make_monitor(body_b["organization"]["id"], body_b["user"]["id"])
    r = client_a.get(f"/monitors/{monitor_id}/tracking")
    assert r.status_code == 404


# ------------------------------- confirm endpoint auth -------------------------------

def test_confirm_requires_a_valid_tracking_api_key():
    client, body = auth_client()
    monitor_id = _make_monitor(body["organization"]["id"], body["user"]["id"])
    public = TestClient(app)

    # No key generated yet for this monitor.
    r_missing = public.post(f"/monitors/{monitor_id}/tracking/confirm",
                            json={"event_type": "purchase", "external_id": "o1"})
    assert r_missing.status_code == 401

    key_resp = client.post(f"/monitors/{monitor_id}/tracking/api-key")
    assert key_resp.status_code == 200
    api_key = key_resp.json()["api_key"]

    r_wrong = public.post(f"/monitors/{monitor_id}/tracking/confirm",
                          headers={"Authorization": "Bearer wrong-key"},
                          json={"event_type": "purchase", "external_id": "o1"})
    assert r_wrong.status_code == 401

    r_ok = public.post(f"/monitors/{monitor_id}/tracking/confirm",
                       headers={"Authorization": f"Bearer {api_key}"},
                       json={"event_type": "purchase", "external_id": "o1", "value_cents": 2500,
                             "currency": "USD"})
    assert r_ok.status_code == 200
    assert r_ok.json()["verified"] is True

    db = SessionLocal()
    try:
        conv = db.query(AnalyticsConversion).filter_by(monitor_id=monitor_id, external_id="o1").first()
        assert conv.verified is True
        assert conv.value_cents == 2500
    finally:
        db.close()


def test_confirm_with_org_as_key_cannot_confirm_a_different_orgs_monitor():
    client_a, body_a = auth_client()
    client_b, body_b = auth_client()
    monitor_a = _make_monitor(body_a["organization"]["id"], body_a["user"]["id"])
    monitor_b = _make_monitor(body_b["organization"]["id"], body_b["user"]["id"])

    key_a = client_a.post(f"/monitors/{monitor_a}/tracking/api-key").json()["api_key"]
    client_b.post(f"/monitors/{monitor_b}/tracking/api-key")   # B has its own key configured
    public = TestClient(app)
    # Org A's key must not confirm a conversion on Org B's monitor, even though B's
    # monitor DOES have a key configured (so this exercises the actual hash
    # comparison, not just "no key set yet").
    r = public.post(f"/monitors/{monitor_b}/tracking/confirm",
                    headers={"Authorization": f"Bearer {key_a}"},
                    json={"event_type": "purchase", "external_id": "x"})
    assert r.status_code == 401
