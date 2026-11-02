"""AI Traffic & Conversion Intelligence: traffic classification, paginated GA4 reports
with data-quality flags, idempotent window-replacing syncs, event mapping, session-level
attribution, honest missing-data states, tenant isolation and API errors. Google is
never called — fetchers and httpx are stubbed."""
import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, Ga4EventMapping, Ga4EventSnapshot, Ga4LandingPageSnapshot,
    Ga4TrafficSnapshot, GoogleAnalyticsConnection,
)
from app.db.session import SessionLocal
from app.services.integrations import ai_intelligence as ai
from app.services.integrations import google_analytics as ga4
from app.services.integrations.classification import classify_source, traffic_class
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="
TODAY = date.today()
K = lambda d: d.strftime("%Y%m%d")   # noqa: E731


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "cs")
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "https://api.example.com/cb")


def _conn(org, user, **over) -> str:
    db = SessionLocal()
    try:
        fields = {"organization_id": org, "connected_by_user_id": user, "status": GA4_STATUS_ACTIVE,
                  "encrypted_refresh_token": token_crypto.encrypt_token("rt"), "ga4_property_id": "123",
                  "ga4_property_name": "Acme", "last_synced_at": datetime.utcnow(),
                  "updated_at": datetime.utcnow(), **over}
        c = GoogleAnalyticsConnection(**fields)
        db.add(c); db.commit()
        return c.id
    finally:
        db.close()


def _traffic(db, org, cid, d, channel, source, medium, sessions, *, engaged=0, revenue=None, campaign="(not set)"):
    db.add(Ga4TrafficSnapshot(organization_id=org, connection_id=cid, date=K(d), channel_grouping=channel,
                              source=source, medium=medium, campaign=campaign,
                              ai_source_label=classify_source(source, medium), sessions=sessions,
                              engaged_sessions=engaged, revenue=revenue))


def _event(db, org, cid, d, name, channel, source, medium, count, revenue=None):
    db.add(Ga4EventSnapshot(organization_id=org, connection_id=cid, date=K(d), event_name=name,
                            channel_grouping=channel, source=source, medium=medium,
                            traffic_class=traffic_class(channel, source, medium),
                            event_count=count, key_events=0, revenue=revenue))


# ------------------------------------------------------------------ classification

@pytest.mark.parametrize("channel,source,medium,expected", [
    ("Referral", "chatgpt.com", "referral", "chatgpt"),
    ("Referral", "chat.openai.com", "referral", "chatgpt"),
    ("Referral", "www.perplexity.ai", "referral", "perplexity"),
    ("Referral", "gemini.google.com", "referral", "gemini"),
    ("Referral", "claude.ai", "referral", "claude"),
    ("Referral", "copilot.microsoft.com", "referral", "copilot"),
    ("Referral", "poe.com", "referral", "other_ai"),
    ("Referral", "chat.deepseek.com", "referral", "other_ai"),
    # Google organic — including any AI Overviews click inside it — is organic search.
    ("Organic Search", "google", "organic", "organic_search"),
    ("Organic Search", "bing", "organic", "organic_search"),
    ("Paid Search", "google", "cpc", "paid_search"),
    ("Direct", "(direct)", "(none)", "direct"),
    ("Referral", "news.example.org", "referral", "referral"),
    ("Organic Social", "facebook.com", "referral", "social"),
    ("Email", "newsletter", "email", "other"),
    ("Unassigned", "(not set)", "(not set)", "other"),
    # Lookalike hosts never become AI.
    ("Referral", "notchatgpt.com", "referral", "referral"),
    ("Referral", "chatgpt.com.evil.example", "referral", "referral"),
])
def test_traffic_classification(channel, source, medium, expected):
    assert traffic_class(channel, source, medium) == expected


def test_missing_attribution_is_never_guessed():
    assert traffic_class(None, None, None) == "other"
    assert traffic_class("", "", "") == "other"


# ------------------------------------------------------------------ GA4 report runner

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _PagedClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append(json)
        return self.pages[len(self.calls) - 1]


def _row(dims, mets):
    return {"dimensionValues": [{"value": d} for d in dims], "metricValues": [{"value": m} for m in mets]}


def test_reports_follow_pagination_and_record_ga4_quality_flags(monkeypatch):
    import httpx
    m = ["5", "1", "2", "9", "30.0", "1", "12.5"]
    page1 = {"rowCount": 3, "rows": [_row(["20261101", "Referral", "chatgpt.com", "referral", "spring"], m)] * 2,
             "metadata": {"subjectToThresholding": True}}
    page2 = {"rowCount": 3, "rows": [_row(["20261101", "Direct", "(direct)", "(none)", "(not set)"], m)],
             "metadata": {"dataLossFromOtherRow": True}}
    client = _PagedClient([_Resp(200, page1), _Resp(200, page2)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    quality = {}
    ga4._REPORT_QUALITY.set(quality)
    rows = asyncio.run(ga4.fetch_daily_traffic("tok", "123", "2026-11-01", "2026-11-01"))
    assert len(rows) == 3 and [c["offset"] for c in client.calls] == [0, 2]
    assert rows[0]["campaign"] == "spring" and rows[0]["revenue"] == 12.5
    assert quality["traffic"] == {"thresholded": True, "sampled": False, "other_row": True, "truncated": False}


def test_events_report_parses_event_counts_and_revenue(monkeypatch):
    import httpx
    body = {"rowCount": 1, "rows": [_row(["20261101", "purchase", "Referral", "chatgpt.com", "referral"], ["3", "3", "240.0"])]}
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _PagedClient([_Resp(200, body)]))
    rows = asyncio.run(ga4.fetch_daily_events("tok", "123", "2026-11-01", "2026-11-01"))
    assert rows == [{"date": "20261101", "event_name": "purchase", "channel_grouping": "Referral",
                     "source": "chatgpt.com", "medium": "referral", "event_count": 3, "key_events": 3,
                     "revenue": 240.0}]


def test_a_rejected_report_is_none_and_a_forbidden_one_raises(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _PagedClient([_Resp(400, {"error": {"status": "INVALID_ARGUMENT"}})]))
    assert asyncio.run(ga4.fetch_daily_events("tok", "123", "2026-11-01", "2026-11-01")) is None
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _PagedClient([_Resp(403, {})]))
    with pytest.raises(ga4.GoogleApiError):
        asyncio.run(ga4.fetch_daily_events("tok", "123", "2026-11-01", "2026-11-01"))


def test_landing_rows_are_classified_by_source():
    rows = ga4._landing_rows([(["20261101", "/pricing", "Referral", "perplexity.ai", "referral"],
                               ["4", "1", "3", "6", "20.0", "1", "0"])])
    assert rows[0]["traffic_class"] == "perplexity" and rows[0]["revenue"] == 0.0


# ------------------------------------------------------------------ sync

def _stub_sync(monkeypatch, traffic, landing=None, events=None, keys=("purchase",), currency="EUR"):
    async def refresh(rt):
        return {"access_token": "at"}
    async def ret(v):
        return v
    monkeypatch.setattr(ga4, "refresh_access_token", refresh)
    monkeypatch.setattr(ga4, "fetch_daily_traffic", lambda *a: ret(traffic))
    monkeypatch.setattr(ga4, "fetch_daily_overview", lambda *a: ret([]))
    monkeypatch.setattr(ga4, "fetch_landing_pages", lambda *a: ret(landing or []))
    monkeypatch.setattr(ga4, "fetch_daily_events", lambda *a: ret(events))
    monkeypatch.setattr(ga4, "fetch_key_event_names", lambda *a: ret(list(keys) if keys is not None else None))
    monkeypatch.setattr(ga4, "fetch_property_currency", lambda *a: ret(currency))


def _count(**c):
    base = {"sessions": 0, "new_users": 0, "engaged_sessions": 0, "views": 0,
            "engagement_duration_sec": 0.0, "key_events": 0, "revenue": None}
    return {**base, **c}


def test_sync_is_idempotent_and_replaces_legacy_rows_inside_its_window(monkeypatch):
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    cid = _conn(org, user)
    db = SessionLocal()
    try:   # a legacy (pre-campaign) row inside the window and one far outside it
        db.add(Ga4TrafficSnapshot(organization_id=org, connection_id=cid, date=K(TODAY), channel_grouping="Direct",
                                  source="(direct)", medium="(none)", ai_source_label="unattributable", sessions=99))
        db.add(Ga4TrafficSnapshot(organization_id=org, connection_id=cid, date=K(TODAY - timedelta(days=400)),
                                  channel_grouping="Direct", source="(direct)", medium="(none)",
                                  ai_source_label="unattributable", sessions=7))
        db.commit()
    finally:
        db.close()
    traffic = [
        {"date": K(TODAY), "channel_grouping": "Direct", "source": "(direct)", "medium": "(none)", "campaign": "(not set)", **_count(sessions=10)},
        {"date": K(TODAY), "channel_grouping": "Referral", "source": "chatgpt.com", "medium": "referral", "campaign": "a", **_count(sessions=3, revenue=50.0)},
        {"date": K(TODAY), "channel_grouping": "Referral", "source": "chatgpt.com", "medium": "referral", "campaign": "a", **_count(sessions=2, revenue=None)},
    ]
    events = [{"date": K(TODAY), "event_name": "purchase", "channel_grouping": "Referral", "source": "chatgpt.com",
               "medium": "referral", "event_count": 2, "key_events": 2, "revenue": 50.0}]
    _stub_sync(monkeypatch, traffic, events=events)
    for _ in range(2):   # the same sync twice must not double anything
        db = SessionLocal()
        try:
            asyncio.run(ga4.run_sync(db, db.get(GoogleAnalyticsConnection, cid)))
        finally:
            db.close()
    db = SessionLocal()
    try:
        rows = db.query(Ga4TrafficSnapshot).filter_by(connection_id=cid).all()
        assert sorted((r.source, r.sessions) for r in rows if r.date == K(TODAY)) == [("(direct)", 10), ("chatgpt.com", 5)]
        assert [r.sessions for r in rows if r.date != K(TODAY)] == [7]       # outside the window: kept
        merged = next(r for r in rows if r.source == "chatgpt.com")
        assert merged.revenue == 50.0 and merged.campaign == "a"
        assert db.query(Ga4EventSnapshot).filter_by(connection_id=cid).count() == 1
        conn = db.get(GoogleAnalyticsConnection, cid)
        assert conn.currency_code == "EUR" and conn.key_event_names == ["purchase"]
        assert conn.key_events_configured is True and conn.events_synced_at is not None
    finally:
        db.close()


def test_a_failed_events_report_keeps_previous_events_and_freshness(monkeypatch):
    _, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    before = datetime.utcnow() - timedelta(days=3)
    cid = _conn(org, user, events_synced_at=before)
    _stub_sync(monkeypatch, [], events=None, keys=None, currency=None)
    db = SessionLocal()
    try:
        conn = db.get(GoogleAnalyticsConnection, cid)
        asyncio.run(ga4.run_sync(db, conn))
        db.refresh(conn)
        assert conn.events_synced_at == before          # never stamped for a failed report
        assert conn.key_event_names is None             # unknown stays unknown
    finally:
        db.close()


def test_switching_property_clears_events_and_mappings(monkeypatch):
    client, body = auth_client()
    org, user = body["organization"]["id"], body["user"]["id"]
    cid = _conn(org, user, currency_code="USD", key_event_names=["purchase"])
    db = SessionLocal()
    try:
        _event(db, org, cid, TODAY, "purchase", "Direct", "(direct)", "(none)", 1)
        db.add(Ga4EventMapping(organization_id=org, connection_id=cid, property_id="123", event_name="purchase",
                               role="conversion", conversion_type="purchase"))
        db.commit()
    finally:
        db.close()

    async def refresh(rt):
        return {"access_token": "at"}
    async def props(tok):
        return [{"property_id": "123", "property_name": "Acme", "account_name": "A"},
                {"property_id": "456", "property_name": "Other", "account_name": "A"}]
    monkeypatch.setattr(ga4, "refresh_access_token", refresh)
    monkeypatch.setattr(ga4, "list_ga4_properties", props)
    assert client.post("/integrations/google/select-property", json={"property_id": "456"}).status_code == 200
    db = SessionLocal()
    try:
        assert db.query(Ga4EventSnapshot).filter_by(connection_id=cid).count() == 0
        assert db.query(Ga4EventMapping).filter_by(connection_id=cid).count() == 0
        conn = db.get(GoogleAnalyticsConnection, cid)
        assert conn.currency_code is None and conn.key_event_names is None
        # select-property schedules an immediate sync; un-schedule it so this shared test
        # DB doesn't hand a "due" connection to another file's scheduler tests.
        conn.next_sync_at = None
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ mappings

def test_mapping_validation_rejects_bad_input():
    with pytest.raises(ai.MappingError):
        ai.validate_mappings([{"event_name": "bad name!", "role": "conversion", "conversion_type": "lead"}])
    with pytest.raises(ai.MappingError):
        ai.validate_mappings([{"event_name": "x", "role": "conversion", "conversion_type": "lead"}] * 2)
    with pytest.raises(ai.MappingError):
        ai.validate_mappings([{"event_name": "x", "role": "conversion"}])        # type required
    with pytest.raises(ai.MappingError):
        ai.validate_mappings([{"event_name": "x", "role": "nonsense"}])
    clean = ai.validate_mappings([{"event_name": "add_to_cart", "role": "funnel_step", "conversion_type": "purchase",
                                   "counts_revenue": True}])
    assert clean[0]["conversion_type"] is None and clean[0]["counts_revenue"] is False   # funnel steps carry neither


def test_mapping_api_saves_replaces_and_is_tenant_isolated():
    client, body = auth_client()
    other, _ = auth_client()
    _conn(body["organization"]["id"], body["user"]["id"])
    r = client.put("/integrations/google/event-mappings", json={"mappings": [
        {"event_name": "generate_lead", "role": "conversion", "conversion_type": "lead"},
        {"event_name": "book_call", "role": "conversion", "conversion_type": "booking", "label": "Demo booked"},
    ]})
    assert r.status_code == 200 and r.json()["conversion_definition"]["mode"] == "mapped"
    assert client.put("/integrations/google/event-mappings",
                      json={"mappings": [{"event_name": "1x", "role": "conversion", "conversion_type": "lead"}]}).status_code == 422
    assert other.put("/integrations/google/event-mappings", json={"mappings": []}).status_code == 404
    r = client.put("/integrations/google/event-mappings", json={"mappings": []})
    assert r.json()["conversion_definition"]["mode"] == "none"


# ------------------------------------------------------------------ attribution

def _seed_period(org, cid):
    db = SessionLocal()
    try:
        d = TODAY - timedelta(days=3)
        _traffic(db, org, cid, d, "Referral", "chatgpt.com", "referral", 40, engaged=30, revenue=300.0)
        _traffic(db, org, cid, d, "Referral", "perplexity.ai", "referral", 10, engaged=5, revenue=0.0)
        _traffic(db, org, cid, d, "Organic Search", "google", "organic", 100, engaged=60, revenue=500.0)
        _traffic(db, org, cid, d, "Direct", "(direct)", "(none)", 50, engaged=20, revenue=0.0)
        _event(db, org, cid, d, "purchase", "Referral", "chatgpt.com", "referral", 4, revenue=300.0)
        _event(db, org, cid, d, "purchase", "Organic Search", "google", "organic", 5, revenue=500.0)
        _event(db, org, cid, d, "generate_lead", "Organic Search", "google", "organic", 3)
        _event(db, org, cid, d, "add_to_cart", "Referral", "chatgpt.com", "referral", 12)
        _event(db, org, cid, d, "page_view", "Direct", "(direct)", "(none)", 900)
        for cls, n in (("chatgpt", 40), ("organic_search", 100), (None, 25)):
            db.add(Ga4LandingPageSnapshot(organization_id=org, connection_id=cid, date=K(d), landing_page="/pricing",
                                          traffic_class=cls, sessions=n, engaged_sessions=n // 2, key_events=1))
        db.commit()
    finally:
        db.close()


def test_without_mapping_or_key_events_conversions_are_not_measured_never_zero():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow(), key_event_names=[])
    _seed_period(org, cid)
    r = client.get("/integrations/google/intelligence").json()
    assert r["conversions"]["status"] == "not_configured"
    assert r["conversions"]["total"] is None and r["overview"]["total_conversions"] is None
    assert all(row["conversions"] is None for row in r["comparison"])


def test_key_events_are_the_fallback_definition_and_attribution_is_by_session_source():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow(), key_event_names=["purchase"],
                currency_code="USD")
    _seed_period(org, cid)
    r = client.get("/integrations/google/intelligence").json()
    c = r["conversions"]
    assert c["definition"]["mode"] == "ga4_key_events" and c["total"] == 9
    by = {row["class"]: row for row in r["comparison"]}
    assert by["chatgpt"]["conversions"] == 4 and by["chatgpt"]["conversions_per_100_sessions"] == 10.0
    assert by["organic_search"]["conversions"] == 5 and by["organic_search"]["conversions_per_100_sessions"] == 5.0
    assert by["chatgpt"]["revenue"] == 300.0 and by["chatgpt"]["share_pct"] == 20.0   # 40 of 200 sessions
    assert c["groups"]["ai"]["sessions"] == 50 and c["groups"]["ai"]["conversions"] == 4
    assert r["overview"]["ai_sessions"] == 50 and r["overview"]["organic_search_sessions"] == 100
    assert [s["event_name"] for s in c["funnel"]] == ["view_item", "add_to_cart", "begin_checkout", "purchase"]
    assert "Session-level" in r["definitions"]["attribution"]


def test_mapped_events_replace_key_events_and_types_are_counted():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow(), key_event_names=["purchase"])
    _seed_period(org, cid)
    client.put("/integrations/google/event-mappings", json={"mappings": [
        {"event_name": "generate_lead", "role": "conversion", "conversion_type": "lead"}]})
    c = client.get("/integrations/google/intelligence").json()["conversions"]
    assert c["definition"]["mode"] == "mapped" and c["total"] == 3 and c["by_type"] == {"lead": 3}


def test_revenue_is_never_shown_without_a_currency():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow(), key_event_names=["purchase"])
    _seed_period(org, cid)
    r = client.get("/integrations/google/intelligence").json()
    assert r["conversions"]["revenue_status"] == "no_currency"
    assert all(row["revenue"] is None for row in r["comparison"])


def test_landing_pages_split_ai_and_organic_and_flag_unsplit_legacy_rows():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow())
    _seed_period(org, cid)
    lp = client.get("/integrations/google/intelligence").json()["landing_pages"]
    page = lp["pages"][0]
    assert (page["sessions"], page["ai_sessions"], page["organic_sessions"]) == (165, 40, 100)
    assert lp["source_split_complete"] is False and lp["unsplit_sessions"] == 25
    table = client.get("/integrations/google/landing-pages").json()["pages"][0]
    assert table["ai_sessions"] == 40 and table["organic_sessions"] == 100


def test_event_catalog_reports_what_is_collected_and_suggests_without_assuming():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow(), key_event_names=["purchase"])
    _seed_period(org, cid)
    r = client.get("/integrations/google/events").json()
    events = {e["event_name"]: e for e in r["events"]}
    assert events["purchase"]["is_key_event"] is True and events["generate_lead"]["is_key_event"] is False
    assert events["generate_lead"]["suggestion"] == {"role": "conversion", "conversion_type": "lead", "counts_revenue": False}
    assert events["page_view"]["automatic"] is True and events["page_view"]["suggestion"] is None
    std = {s["event_name"]: s["status"] for s in r["standard_events"]}
    assert std["purchase"] == "collected" and std["booking_completed"] == "not_seen"


def test_unknown_key_events_are_reported_as_unknown():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], events_synced_at=datetime.utcnow())
    _seed_period(org, cid)
    r = client.get("/integrations/google/events").json()
    assert r["key_events_known"] is False and r["events"][0]["is_key_event"] is None


def test_freshness_quality_and_partial_coverage_are_reported():
    client, body = auth_client()
    org = body["organization"]["id"]
    cid = _conn(org, body["user"]["id"], last_synced_at=datetime.utcnow() - timedelta(days=4),
                data_quality={"traffic": {"thresholded": True, "sampled": False, "other_row": False, "truncated": False}})
    _seed_period(org, cid)
    f = client.get("/integrations/google/intelligence").json()["freshness"]
    assert f["stale"] is True and f["partial_range"] is True
    assert f["data_quality"]["traffic"]["thresholded"] is True
    assert f["processing_lag_from"] is not None


def test_api_errors_and_isolation():
    client, body = auth_client()
    other, _ = auth_client()
    assert client.get("/integrations/google/intelligence").status_code == 404      # no connection
    cid = _conn(body["organization"]["id"], body["user"]["id"], last_synced_at=None)
    r = client.get("/integrations/google/intelligence").json()
    assert r["synced"] is False and "overview" not in r                            # never fabricated
    assert client.get("/integrations/google/intelligence", params={"start_date": "2026-13-01"}).status_code == 422
    assert client.get("/integrations/google/intelligence",
                      params={"start_date": "2026-11-02", "end_date": "2026-11-01"}).status_code == 422
    assert other.get("/integrations/google/intelligence").status_code == 404
    assert other.get("/integrations/google/events").status_code == 404
    assert cid
