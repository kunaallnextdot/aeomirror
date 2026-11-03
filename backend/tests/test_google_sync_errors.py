"""P0-4 regression: transient Google failures (network error, timeout, 429, 5xx, a
rate-limit 403) never disable GA4 or Search Console syncing — the connection stays
active and the scheduler retries it with exponential backoff (honouring Retry-After);
permanent failures (revoked grant, real permission 403, undecryptable token) stop it
until a user acts. Exercised at the real HTTP layer with httpx.MockTransport (no
network): the token endpoint, the GA4 Data API and the Search Console API."""
import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, GA4_STATUS_ERROR, JOB_COMPLETED, JOB_PENDING, GoogleAnalyticsConnection, ScheduledJob,
    SearchConsoleConnection,
)
from app.db.session import SessionLocal
from app.monitoring import runner, scheduler
from app.services.integrations import google_analytics as ga4
from app.services.integrations import google_errors as ge
from app.services.integrations import search_console as gsc
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="
_REAL_CLIENT = httpx.AsyncClient
_REAL_SEND = httpx.AsyncClient.send     # captured before conftest's no-Google guard patches it


class _MockedClient(_REAL_CLIENT):
    """Always built on a MockTransport (below), so it can never reach the network;
    it uses the unguarded send so the fake Google can answer."""

    async def send(self, request, *args, **kwargs):
        return await _REAL_SEND(self, request, *args, **kwargs)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "cs")


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    yield
    db = SessionLocal()
    try:
        db.query(ScheduledJob).filter(ScheduledJob.kind.in_(("ga4_sync", "gsc_sync")),
                                      ScheduledJob.status == JOB_PENDING).delete(synchronize_session=False)
        db.query(SearchConsoleConnection).update({SearchConsoleConnection.next_sync_at: None})
        db.query(GoogleAnalyticsConnection).update({GoogleAnalyticsConnection.next_sync_at: None})
        db.commit()
    finally:
        db.close()


class Google:
    """A fake Google: `token` / `report` / `gsc` are lists of responses (or exceptions)
    served in order; the last one repeats."""

    def __init__(self, token=None, report=None, gsc=None):
        self.token = token or [httpx.Response(200, json={"access_token": "at"})]
        self.report = report or [httpx.Response(200, json={"rows": [], "rowCount": 0})]
        self.gsc = gsc or [httpx.Response(200, json={"rows": []})]
        self.calls = {"token": 0, "report": 0, "gsc": 0}

    def _next(self, kind):
        seq = getattr(self, kind)
        r = seq[min(self.calls[kind], len(seq) - 1)]
        self.calls[kind] += 1
        if isinstance(r, Exception):
            raise r
        return r

    def handler(self, request: httpx.Request):
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return self._next("token")
        if "analyticsdata.googleapis.com" in url:
            return self._next("report")
        if "searchAnalytics" in url:
            return self._next("gsc")
        return httpx.Response(200, json={})     # admin API (key events, currency): empty

    def install(self, monkeypatch):
        transport = httpx.MockTransport(self.handler)
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _MockedClient(*a, transport=transport, **k))
        return self


def _ga4_conn():
    _, body = auth_client()
    db = SessionLocal()
    try:
        c = GoogleAnalyticsConnection(
            organization_id=body["organization"]["id"], connected_by_user_id=body["user"]["id"],
            encrypted_refresh_token=token_crypto.encrypt_token("rt"), status=GA4_STATUS_ACTIVE,
            ga4_property_id="123", connected_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _gsc_conn():
    _, body = auth_client()
    db = SessionLocal()
    try:
        c = SearchConsoleConnection(
            organization_id=body["organization"]["id"], connected_by_user_id=body["user"]["id"],
            encrypted_refresh_token=token_crypto.encrypt_token("rt"), status=GA4_STATUS_ACTIVE,
            site_url="sc-domain:example.com")
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _queued(db, cid, when=None) -> bool:
    """Would the scheduler enqueue THIS connection's sync at `when`?"""
    scheduler.enqueue_due_ga4_syncs(db, when)
    job = scheduler.has_active_ga4_sync_job(db, cid)
    if job is not None:
        db.delete(job)
        db.commit()
    return job is not None


def _sync(model, cid, fn):
    db = SessionLocal()
    try:
        c = db.get(model, cid)
        err = None
        try:
            asyncio.run(fn(db, c))
        except Exception as e:   # noqa: BLE001
            err = e
        db.refresh(c)
        return c, err
    finally:
        db.close()


TRANSIENT = [
    ("timeout", {"report": [httpx.ReadTimeout("slow")]}, "google_unavailable"),
    ("500", {"report": [httpx.Response(500, json={"error": {"status": "INTERNAL"}})]}, "google_unavailable"),
    ("429", {"report": [httpx.Response(429, headers={"Retry-After": "7200"})]}, "quota_exceeded"),
    ("rate-limit 403", {"report": [httpx.Response(403, json={"error": {"status": "RESOURCE_EXHAUSTED"}})]},
     "quota_exceeded"),
    ("401 from the Data API", {"report": [httpx.Response(401)]}, "google_unavailable"),
    ("token endpoint 503", {"token": [httpx.Response(503)]}, "google_unavailable"),
    ("token endpoint network error", {"token": [httpx.ConnectError("down")]}, "network_error"),
    ("token endpoint 429", {"token": [httpx.Response(429)]}, "quota_exceeded"),
]


@pytest.mark.parametrize("name,responses,code", TRANSIENT, ids=[t[0] for t in TRANSIENT])
def test_transient_ga4_failures_keep_the_connection_active_and_back_off(monkeypatch, name, responses, code):
    Google(**responses).install(monkeypatch)
    cid = _ga4_conn()
    before = datetime.utcnow()
    c, err = _sync(GoogleAnalyticsConnection, cid, ga4.run_sync)
    assert isinstance(err, ge.SyncDeferred)
    assert (c.status, c.last_sync_status, c.last_sync_error_code, c.sync_failures) == (
        GA4_STATUS_ACTIVE, "error", code, 1)
    assert c.next_sync_at >= before + timedelta(minutes=11)        # 15 min −20 % jitter, at least
    if name == "429":
        assert c.next_sync_at >= before + timedelta(seconds=7200)  # Retry-After honoured


PERMANENT = [
    ("revoked grant", {"token": [httpx.Response(400, json={"error": "invalid_grant"})]}, "auth_revoked"),
    ("client rejected", {"token": [httpx.Response(401, json={"error": "invalid_client"})]}, "auth_failed"),
    ("permission 403", {"report": [httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})]},
     "property_inaccessible"),
]


@pytest.mark.parametrize("name,responses,code", PERMANENT, ids=[t[0] for t in PERMANENT])
def test_permanent_ga4_failures_stop_syncing_until_a_user_acts(monkeypatch, name, responses, code):
    g = Google(**responses).install(monkeypatch)
    cid = _ga4_conn()
    c, err = _sync(GoogleAnalyticsConnection, cid, ga4.run_sync)
    assert isinstance(err, ge.SyncStopped)
    assert (c.status, c.last_sync_error_code) == (GA4_STATUS_ERROR, code)
    db = SessionLocal()
    try:   # never re-enqueued, however long we wait
        assert not _queued(db, cid, datetime.utcnow() + timedelta(days=30))
    finally:
        db.close()
    calls = dict(g.calls)
    _sync(GoogleAnalyticsConnection, cid, ga4.run_sync)
    assert g.calls == calls                                          # no further Google call


def test_an_undecryptable_token_is_permanent(monkeypatch):
    g = Google().install(monkeypatch)
    cid = _ga4_conn()
    db = SessionLocal()
    try:
        db.get(GoogleAnalyticsConnection, cid).encrypted_refresh_token = "not-a-fernet-token"
        db.commit()
    finally:
        db.close()
    c, err = _sync(GoogleAnalyticsConnection, cid, ga4.run_sync)
    assert isinstance(err, ge.SyncStopped) and c.last_sync_error_code == "credential_unavailable"
    assert g.calls["token"] == 0


def test_after_a_transient_failure_the_scheduler_retries_and_a_success_recovers(monkeypatch):
    g = Google(report=[httpx.Response(503), httpx.Response(200, json={"rows": [], "rowCount": 0})]).install(monkeypatch)
    cid = _ga4_conn()
    db = SessionLocal()
    try:
        c = db.get(GoogleAnalyticsConnection, cid)
        c.next_sync_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        job = scheduler.enqueue_manual_ga4_sync(db, c)
        job.status, job.started_at, job.attempts = "running", datetime.utcnow(), 1
        db.commit()
        asyncio.run(runner.process_job(db, job))
        db.refresh(job)
        db.refresh(c)
        assert job.status == JOB_COMPLETED                      # the connection owns the retry
        assert c.status == GA4_STATUS_ACTIVE and c.sync_failures == 1
        assert not _queued(db, cid)                             # not before the backoff expires
        later = c.next_sync_at + timedelta(seconds=1)
        scheduler.enqueue_due_ga4_syncs(db, later)
        job = scheduler.has_active_ga4_sync_job(db, cid)
        assert job is not None                                  # retried once it does
        job.status, job.started_at, job.attempts = "running", later, 1
        db.commit()
        asyncio.run(runner.process_job(db, job))
        db.refresh(c)
        assert (c.last_sync_status, c.last_sync_error_code, c.sync_failures) == ("success", None, 0)
        assert c.next_sync_at > datetime.utcnow() + timedelta(hours=1)
    finally:
        db.close()


def test_an_unexpected_error_backs_off_instead_of_requeueing_every_tick(monkeypatch):
    async def boom(db, connection):
        raise ValueError("bug")
    monkeypatch.setattr(ga4, "run_sync", boom)
    cid = _ga4_conn()
    db = SessionLocal()
    try:
        c = db.get(GoogleAnalyticsConnection, cid)
        c.next_sync_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        job = scheduler.enqueue_manual_ga4_sync(db, c)
        job.status, job.started_at, job.attempts = "running", datetime.utcnow(), 1
        db.commit()
        asyncio.run(runner.process_job(db, job))
        db.refresh(c)
        assert c.last_sync_error_code == "sync_failed" and c.next_sync_at > datetime.utcnow()
        assert not _queued(db, cid)
    finally:
        db.close()


def test_a_dashboard_view_during_an_outage_never_disables_syncing(monkeypatch):
    Google(token=[httpx.Response(503)]).install(monkeypatch)
    cid = _ga4_conn()
    db = SessionLocal()
    try:
        with pytest.raises(ge.GoogleTransientError):
            asyncio.run(ga4.get_valid_access_token(db, db.get(GoogleAnalyticsConnection, cid)))
        c = db.get(GoogleAnalyticsConnection, cid)
        db.refresh(c)
        assert c.status == GA4_STATUS_ACTIVE and c.last_sync_error_code is None
    finally:
        db.close()


def test_the_properties_route_reports_a_temporary_outage_as_503(monkeypatch):
    Google(token=[httpx.Response(500)]).install(monkeypatch)
    client, body = auth_client()
    db = SessionLocal()
    try:
        db.add(GoogleAnalyticsConnection(
            organization_id=body["organization"]["id"], connected_by_user_id=body["user"]["id"],
            encrypted_refresh_token=token_crypto.encrypt_token("rt"), status=GA4_STATUS_ACTIVE,
            ga4_property_id="1", connected_at=datetime.utcnow(), updated_at=datetime.utcnow()))
        db.commit()
    finally:
        db.close()
    r = client.get("/integrations/google/properties")
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]
    s = client.get("/integrations/google/status").json()
    assert s["status"] == GA4_STATUS_ACTIVE and "next_sync_at" in s and s["sync_failures"] == 0


GSC_CASES = [
    ("429", [httpx.Response(429)], ge.SyncDeferred, GA4_STATUS_ACTIVE, "quota_exceeded"),
    ("rate-limit 403", [httpx.Response(403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}})],
     ge.SyncDeferred, GA4_STATUS_ACTIVE, "quota_exceeded"),
    ("500", [httpx.Response(500)], ge.SyncDeferred, GA4_STATUS_ACTIVE, "google_unavailable"),
    ("timeout", [httpx.ReadTimeout("slow")], ge.SyncDeferred, GA4_STATUS_ACTIVE, "google_unavailable"),
    ("permission 403", [httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})],
     ge.SyncStopped, GA4_STATUS_ERROR, "property_inaccessible"),
]


@pytest.mark.parametrize("name,responses,exc,status,code", GSC_CASES, ids=[t[0] for t in GSC_CASES])
def test_search_console_classifies_failures_the_same_way(monkeypatch, name, responses, exc, status, code):
    Google(gsc=responses).install(monkeypatch)
    cid = _gsc_conn()
    c, err = _sync(SearchConsoleConnection, cid, gsc.run_sync)
    assert isinstance(err, exc) and (c.status, c.last_sync_error_code) == (status, code)


def test_search_console_recovers_after_a_transient_failure(monkeypatch):
    Google(gsc=[httpx.Response(503), httpx.Response(200, json={"rows": []})]).install(monkeypatch)
    cid = _gsc_conn()
    c, _ = _sync(SearchConsoleConnection, cid, gsc.run_sync)
    assert c.sync_failures == 1
    c, err = _sync(SearchConsoleConnection, cid, gsc.run_sync)
    assert err is None and (c.status, c.last_sync_status, c.sync_failures) == (GA4_STATUS_ACTIVE, "success", 0)


def test_backoff_grows_exponentially_with_jitter_and_caps_at_a_day():
    lo = [ge.backoff_seconds(n, rand=lambda: 0.0) for n in range(1, 10)]
    hi = [ge.backoff_seconds(n, rand=lambda: 1.0) for n in range(1, 10)]
    assert lo[:4] == pytest.approx([720.0, 1440.0, 2880.0, 5760.0])  # 15, 30, 60, 120 min × 0.8
    assert hi[0] == pytest.approx(1080.0) and max(hi) <= 24 * 3600 * 1.2
    assert lo[-1] == pytest.approx(24 * 3600 * 0.8)                  # capped
    assert ge.backoff_seconds(1, retry_after=5000, rand=lambda: 0.0) == 5000


def test_retry_after_accepts_seconds_and_http_dates():
    assert ge.retry_after_seconds(httpx.Response(429, headers={"Retry-After": "120"})) == 120
    future = (datetime.utcnow() + timedelta(minutes=10)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 500 < ge.retry_after_seconds(httpx.Response(429, headers={"Retry-After": future})) <= 600
    assert ge.retry_after_seconds(httpx.Response(429)) is None
