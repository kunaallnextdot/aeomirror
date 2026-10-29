"""GA4 Website Intelligence dashboard: the new report parsers (_parse_count_metrics,
_parse_daily_overview_report, _parse_landing_page_report,
_parse_overview_comparison_report) and the new fetch_* functions' HTTP-level error
handling (403/429/transport failure), plus run_sync's secondary-table upserts and
key_events_configured refresh. Metric-index alignment and the non-additive
totalUsers handling are the sensitive surface here — see Ga4TrafficSnapshot's and
Ga4DailyOverview's docstrings in app/db/models.py for why totalUsers is handled
differently from every other metric."""
import asyncio
from datetime import date, datetime

import pytest

from app.db.models import (
    GA4_STATUS_ACTIVE, Ga4DailyOverview, Ga4LandingPageSnapshot,
    GoogleAnalyticsConnection,
)
from app.core import token_crypto
from app.config import settings
from app.db.session import SessionLocal
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="


@pytest.fixture(autouse=True)
def _ga4_key(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)


def _metric_row(*values: str) -> dict:
    return {"metricValues": [{"value": v} for v in values]}


def _dim(row: dict, *values: str) -> dict:
    row["dimensionValues"] = [{"value": v} for v in values]
    return row


# ------------------------------- _parse_count_metrics -------------------------------

def test_parse_count_metrics_maps_by_fixed_index():
    mets = ["5", "3", "4", "12", "240.5", "1"]
    out = ga4._parse_count_metrics(mets)
    assert out == {"sessions": 5, "new_users": 3, "engaged_sessions": 4,
                    "views": 12, "engagement_duration_sec": 240.5, "key_events": 1}


def test_parse_count_metrics_never_raises_on_missing_or_malformed_values():
    assert ga4._parse_count_metrics([]) == {
        "sessions": 0, "new_users": 0, "engaged_sessions": 0,
        "views": 0, "engagement_duration_sec": 0.0, "key_events": 0,
    }
    assert ga4._parse_count_metrics(["not-a-number", "", None, "x", "y", "z"]) == {
        "sessions": 0, "new_users": 0, "engaged_sessions": 0,
        "views": 0, "engagement_duration_sec": 0.0, "key_events": 0,
    }


# ------------------------------- _parse_daily_overview_report -------------------------------

def test_parse_daily_overview_report_reads_total_users_first_then_count_metrics():
    payload = {"rows": [
        _dim(_metric_row("50", "20", "35", "40", "150", "500.0", "3"), "20261027"),
    ]}
    rows = ga4._parse_daily_overview_report(payload)
    assert rows == [{
        "date": "20261027", "total_users": 50,
        "sessions": 20, "new_users": 35, "engaged_sessions": 40,
        "views": 150, "engagement_duration_sec": 500.0, "key_events": 3,
    }]


def test_parse_daily_overview_report_skips_rows_with_no_dimensions_or_metrics():
    assert ga4._parse_daily_overview_report({"rows": [{"dimensionValues": [], "metricValues": []}]}) == []
    assert ga4._parse_daily_overview_report({}) == []


# ------------------------------- _parse_landing_page_report -------------------------------

def test_parse_landing_page_report_pairs_date_and_landing_page():
    payload = {"rows": [
        _dim(_metric_row("10", "4", "6", "15", "80.0", "0"), "20261027", "/pricing"),
    ]}
    rows = ga4._parse_landing_page_report(payload)
    assert rows == [{
        "date": "20261027", "landing_page": "/pricing",
        "sessions": 10, "new_users": 4, "engaged_sessions": 6,
        "views": 15, "engagement_duration_sec": 80.0, "key_events": 0,
    }]


def test_parse_landing_page_report_defaults_empty_path_to_not_set():
    payload = {"rows": [_dim(_metric_row("1", "0", "0", "1", "0", "0"), "20261027", "")]}
    rows = ga4._parse_landing_page_report(payload)
    assert rows[0]["landing_page"] == "(not set)"


# ------------------------------- _parse_overview_comparison_report -------------------------------

def test_parse_overview_comparison_report_splits_current_and_previous_by_daterange_name():
    payload = {"rows": [
        _dim(_metric_row("120", "80", "50", "60", "300", "900.0", "5"), "current"),
        _dim(_metric_row("100", "70", "40", "55", "280", "850.0", "4"), "previous"),
    ]}
    out = ga4._parse_overview_comparison_report(payload)
    assert out["current"]["total_users"] == 120
    assert out["current"]["sessions"] == 80
    assert out["previous"]["total_users"] == 100
    assert out["previous"]["key_events"] == 4


def test_parse_overview_comparison_report_never_mixes_current_and_previous_numbers():
    """A single malformed/unlabeled row must not silently populate a period with
    the other period's numbers, and a missing period must come back as honest
    zeros, never None (callers should not need a null check for every field)."""
    payload = {"rows": [_dim(_metric_row("120", "80", "50", "60", "300", "900.0", "5"), "current")]}
    out = ga4._parse_overview_comparison_report(payload)
    assert out["current"]["sessions"] == 80
    assert out["previous"] == {"total_users": 0, "sessions": 0, "new_users": 0,
                                "engaged_sessions": 0, "views": 0,
                                "engagement_duration_sec": 0.0, "key_events": 0}


def test_parse_overview_comparison_report_ignores_an_unrecognized_daterange_label():
    payload = {"rows": [_dim(_metric_row("1", "1", "1", "1", "1", "1", "1"), "date_range_7")]}
    out = ga4._parse_overview_comparison_report(payload)
    assert out["current"]["sessions"] == 0
    assert out["previous"]["sessions"] == 0


# ------------------------------- fetch_* HTTP-level error handling -------------------------------
# httpx is mocked at the AsyncClient level (this module's established convention mocks
# the fetch_* functions themselves for higher-level tests — see test_ga4_sync.py — so
# these are the first tests of the actual HTTP call sites' 403/429/malformed-body
# handling, which previously had no direct coverage at all).

class _FakeResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("no body")
        return self._json_body


class _FakeAsyncClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        if self._raises:
            raise self._raises
        return self._response

    async def get(self, *a, **k):
        if self._raises:
            raise self._raises
        return self._response


def _patch_httpx_client(monkeypatch, *, response=None, raises=None):
    import httpx
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(response=response, raises=raises),
    )


def test_fetch_daily_overview_raises_on_403():
    import httpx
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(403))
        with pytest.raises(ga4.GoogleApiError) as exc_info:
            asyncio.run(ga4.fetch_daily_overview("tok", "123", "2026-01-01", "2026-01-07"))
        assert exc_info.value.status_code == 403


def test_fetch_daily_overview_raises_on_429():
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(429))
        with pytest.raises(ga4.GoogleApiError) as exc_info:
            asyncio.run(ga4.fetch_daily_overview("tok", "123", "2026-01-01", "2026-01-07"))
        assert exc_info.value.status_code == 429


def test_fetch_daily_overview_returns_none_on_transport_error():
    import httpx
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, raises=httpx.ConnectError("no network"))
        result = asyncio.run(ga4.fetch_daily_overview("tok", "123", "2026-01-01", "2026-01-07"))
        assert result is None


def test_fetch_daily_overview_returns_none_on_malformed_body():
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(200, json_body=None))
        result = asyncio.run(ga4.fetch_daily_overview("tok", "123", "2026-01-01", "2026-01-07"))
        assert result is None


def test_fetch_daily_overview_parses_a_successful_response():
    payload = {"rows": [_dim(_metric_row("10", "5", "3", "4", "20", "60.0", "0"), "20260101")]}
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(200, json_body=payload))
        result = asyncio.run(ga4.fetch_daily_overview("tok", "123", "2026-01-01", "2026-01-01"))
        assert result == [{"date": "20260101", "total_users": 10, "sessions": 5,
                            "new_users": 3, "engaged_sessions": 4, "views": 20,
                            "engagement_duration_sec": 60.0, "key_events": 0}]


def test_fetch_landing_pages_raises_on_403():
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(403))
        with pytest.raises(ga4.GoogleApiError):
            asyncio.run(ga4.fetch_landing_pages("tok", "123", "2026-01-01", "2026-01-07"))


def test_fetch_overview_with_comparison_parses_both_periods():
    payload = {"rows": [
        _dim(_metric_row("10", "5", "3", "4", "20", "60.0", "1"), "current"),
        _dim(_metric_row("8", "4", "2", "3", "15", "45.0", "0"), "previous"),
    ]}
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(200, json_body=payload))
        result = asyncio.run(ga4.fetch_overview_with_comparison(
            "tok", "123", current_start="2026-01-08", current_end="2026-01-14",
            previous_start="2026-01-01", previous_end="2026-01-07",
        ))
        assert result["current"]["total_users"] == 10
        assert result["previous"]["total_users"] == 8


def test_overview_request_never_asks_for_daterange_as_a_dimension(monkeypatch):
    """Regression for the production /overview failure: `dateRange` is not a
    requestable dimension in the GA4 Data API schema — GA4 adds that column to the
    response itself when a request has multiple dateRanges. Requesting it
    explicitly is rejected with a 400, which surfaced as "Could not load your
    Google Analytics overview right now." on every page load."""
    import httpx
    captured = {}

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, url, *, headers=None, json=None, **k):
            captured["body"] = json
            return _FakeResponse(200, json_body={"rows": []})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _CapturingClient())
    asyncio.run(ga4.fetch_overview_with_comparison(
        "tok", "123", current_start="2026-01-08", current_end="2026-01-14",
        previous_start="2026-01-01", previous_end="2026-01-07",
    ))
    requested = [d["name"] for d in captured["body"].get("dimensions", [])]
    assert "dateRange" not in requested
    assert [r["name"] for r in captured["body"]["dateRanges"]] == ["current", "previous"]


def test_parse_overview_comparison_report_finds_the_auto_added_daterange_column_by_header():
    payload = {
        "dimensionHeaders": [{"name": "dateRange"}],
        "rows": [
            _dim(_metric_row("12", "6", "3", "4", "20", "60.0", "1"), "previous"),
            _dim(_metric_row("30", "15", "9", "11", "50", "90.0", "2"), "current"),
        ],
    }
    out = ga4._parse_overview_comparison_report(payload)
    assert out["current"]["total_users"] == 30
    assert out["previous"]["total_users"] == 12


def test_a_rejected_report_logs_googles_reason_but_never_the_token(caplog):
    error_body = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                            "message": "Field dateRange is not a valid dimension."}}
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(400, json_body=error_body))
        with caplog.at_level("WARNING", logger="app.integrations.google_analytics"):
            result = asyncio.run(ga4.fetch_overview_with_comparison(
                "secret-access-token", "123", current_start="2026-01-08",
                current_end="2026-01-14", previous_start="2026-01-01",
                previous_end="2026-01-07",
            ))
    assert result is None
    assert "INVALID_ARGUMENT: Field dateRange is not a valid dimension." in caplog.text
    assert "secret-access-token" not in caplog.text


def test_fetch_key_events_configured_true_when_list_nonempty():
    payload = {"keyEvents": [{"eventName": "purchase"}]}
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(200, json_body=payload))
        result = asyncio.run(ga4.fetch_key_events_configured("tok", "123"))
        assert result is True


def test_fetch_key_events_configured_false_when_list_empty():
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(200, json_body={}))
        result = asyncio.run(ga4.fetch_key_events_configured("tok", "123"))
        assert result is False


def test_fetch_key_events_configured_none_on_failure_means_unknown_not_false():
    with pytest.MonkeyPatch.context() as mp:
        _patch_httpx_client(mp, response=_FakeResponse(500))
        result = asyncio.run(ga4.fetch_key_events_configured("tok", "123"))
        assert result is None


# ------------------------------- run_sync: secondary tables + key_events_configured -------------------------------

_TODAY_KEY = date.today().strftime("%Y%m%d")


def _connection_id(org_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        conn = GoogleAnalyticsConnection(
            organization_id=org_id, connected_by_user_id=user_id,
            encrypted_refresh_token=token_crypto.encrypt_token("rt-secret"),
            status=GA4_STATUS_ACTIVE, ga4_property_id="123456",
            connected_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(conn)
        db.commit()
        return conn.id
    finally:
        db.close()


def _patch_refresh_ok(monkeypatch):
    async def fake_refresh(refresh_token):
        return {"access_token": "at-fresh"}
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)


def test_run_sync_upserts_daily_overview_and_landing_page_snapshots_and_refreshes_key_events_flag(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_traffic(token, property_id, start, end):
        return []
    async def fake_overview(token, property_id, start, end):
        return [{"date": _TODAY_KEY, "total_users": 7, "sessions": 5, "new_users": 3,
                  "engaged_sessions": 4, "views": 10, "engagement_duration_sec": 30.0,
                  "key_events": 1}]
    async def fake_landing(token, property_id, start, end):
        return [{"date": _TODAY_KEY, "landing_page": "/", "sessions": 5, "new_users": 3,
                  "engaged_sessions": 4, "views": 10, "engagement_duration_sec": 30.0,
                  "key_events": 1}]
    async def fake_key_events(token, property_id):
        return True
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_traffic)
    monkeypatch.setattr(ga4, "fetch_daily_overview", fake_overview)
    monkeypatch.setattr(ga4, "fetch_landing_pages", fake_landing)
    monkeypatch.setattr(ga4, "fetch_key_events_configured", fake_key_events)

    db = SessionLocal()
    try:
        conn = db.get(GoogleAnalyticsConnection, conn_id)
        asyncio.run(ga4.run_sync(db, conn))

        overview = db.query(Ga4DailyOverview).filter_by(connection_id=conn_id).all()
        assert len(overview) == 1
        assert overview[0].total_users == 7

        landing = db.query(Ga4LandingPageSnapshot).filter_by(connection_id=conn_id).all()
        assert len(landing) == 1
        assert landing[0].landing_page == "/"

        db.refresh(conn)
        assert conn.key_events_configured is True
    finally:
        db.close()


def test_run_sync_secondary_report_failure_does_not_fail_the_whole_sync(monkeypatch):
    """The primary by-channel traffic report is the most important dataset (AI
    Traffic). A transient failure on the daily-overview or landing-page report must
    not discard it."""
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_traffic(token, property_id, start, end):
        return [{"date": _TODAY_KEY, "channel_grouping": "Direct", "source": "(direct)",
                  "medium": "(none)", "sessions": 3, "new_users": 1, "engaged_sessions": 2,
                  "views": 5, "engagement_duration_sec": 10.0, "key_events": 0}]
    async def fake_overview_fails(token, property_id, start, end):
        return None
    async def fake_landing_fails(token, property_id, start, end):
        return None
    async def fake_key_events_unknown(token, property_id):
        return None
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_traffic)
    monkeypatch.setattr(ga4, "fetch_daily_overview", fake_overview_fails)
    monkeypatch.setattr(ga4, "fetch_landing_pages", fake_landing_fails)
    monkeypatch.setattr(ga4, "fetch_key_events_configured", fake_key_events_unknown)

    db = SessionLocal()
    try:
        conn = db.get(GoogleAnalyticsConnection, conn_id)
        result = asyncio.run(ga4.run_sync(db, conn))
        assert result["rows"] == 1
        db.refresh(conn)
        assert conn.last_sync_status == "success"
        assert conn.key_events_configured is None   # never flipped to a guessed value
        assert db.query(Ga4DailyOverview).filter_by(connection_id=conn_id).count() == 0
        assert db.query(Ga4LandingPageSnapshot).filter_by(connection_id=conn_id).count() == 0
    finally:
        db.close()
