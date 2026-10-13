"""GA4 sync: the service-layer run_sync() (app/services/integrations/
google_analytics.py), its scheduler enqueue (app/monitoring/scheduler.py), and the
worker's job dispatch (app/monitoring/runner.py::process_job). Idempotency, error
classification, and the claim-based job queue's existing guarantees are the
sensitive surface here — Google HTTP calls are monkeypatched throughout."""
import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.config import settings
from app.core import token_crypto
from app.db.models import (
    GA4_STATUS_ACTIVE, GA4_STATUS_ERROR, GA4_STATUS_PENDING_PROPERTY, JOB_COMPLETED,
    JOB_FAILED, JOB_PENDING, Ga4TrafficSnapshot, GoogleAnalyticsConnection, ScheduledJob,
)
from app.db.session import SessionLocal
from app.monitoring import runner, scheduler
from app.services.integrations import google_analytics as ga4
from tests.authutil import auth_client

FAKE_KEY = "zxhjMoSGw8Hu9dZ0qVgN6W7AUHHho9mz5igY2ZvQ4oE="

# Real GA4 only ever returns rows inside the requested [start, end] range — a
# fixed historical date would fall outside a later incremental sync's shorter
# lookback window, so these use "today" (matching what run_sync's own date.today()
# anchors its window to) to stay realistic across both an initial and incremental sync.
_TODAY_KEY = date.today().strftime("%Y%m%d")

ROWS_PAGE_1 = [
    {"date": _TODAY_KEY, "channel_grouping": "Referral", "source": "chatgpt.com",
     "medium": "referral", "sessions": 5},
    {"date": _TODAY_KEY, "channel_grouping": "Organic Search", "source": "google",
     "medium": "organic", "sessions": 40},
]
ROWS_PAGE_1_REVISED = [
    {"date": _TODAY_KEY, "channel_grouping": "Referral", "source": "chatgpt.com",
     "medium": "referral", "sessions": 9},   # GA4 revised the same-day count upward
    {"date": _TODAY_KEY, "channel_grouping": "Organic Search", "source": "google",
     "medium": "organic", "sessions": 40},
]


def _connection_id(org_id: str, user_id: str, *, status=GA4_STATUS_ACTIVE,
                   property_id: str | None = "123456", last_synced_at=None) -> str:
    """Creates and commits a connection in its OWN session, returning just the id —
    every test re-loads it (`_load`) in whichever session it actually uses, since a
    SQLAlchemy object detaches the moment its creating session closes."""
    db = SessionLocal()
    try:
        conn = GoogleAnalyticsConnection(
            organization_id=org_id, connected_by_user_id=user_id,
            encrypted_refresh_token=token_crypto.encrypt_token("rt-secret"),
            status=status, ga4_property_id=property_id, connected_at=datetime.utcnow(),
            last_synced_at=last_synced_at, updated_at=datetime.utcnow(),
        )
        db.add(conn)
        db.commit()
        return conn.id
    finally:
        db.close()


def _load(db, conn_id: str) -> GoogleAnalyticsConnection:
    return db.get(GoogleAnalyticsConnection, conn_id)


def _patch_refresh_ok(monkeypatch):
    async def fake_refresh(refresh_token):
        return {"access_token": "at-fresh"}
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)


@pytest.fixture(autouse=True)
def _ga4_key(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", FAKE_KEY)


# ------------------------------- run_sync -------------------------------

def test_run_sync_requires_active_status_and_property():
    db = SessionLocal()
    try:
        conn = GoogleAnalyticsConnection(
            organization_id="org-x", connected_by_user_id="u-x",
            status=GA4_STATUS_PENDING_PROPERTY, ga4_property_id=None,
        )
        with pytest.raises(RuntimeError):
            asyncio.run(ga4.run_sync(db, conn))
    finally:
        db.close()


def test_run_sync_upserts_rows_and_classifies_them(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_fetch(token, property_id, start, end):
        assert property_id == "123456"
        return ROWS_PAGE_1
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        result = asyncio.run(ga4.run_sync(db, conn))
        assert result["rows"] == 2
        rows = db.query(Ga4TrafficSnapshot).filter_by(connection_id=conn_id).all()
        by_source = {r.source: r for r in rows}
        assert by_source["chatgpt.com"].ai_source_label == "chatgpt"
        assert by_source["chatgpt.com"].sessions == 5
        assert by_source["google"].ai_source_label == "unattributable"

        assert conn.last_sync_status == "success"
        assert conn.last_synced_at is not None
        assert conn.next_sync_at > datetime.utcnow()
    finally:
        db.close()


def test_run_sync_is_idempotent_and_updates_revised_counts_without_duplicating(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_fetch_1(token, property_id, start, end):
        return ROWS_PAGE_1
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch_1)
    db = SessionLocal()
    try:
        asyncio.run(ga4.run_sync(db, _load(db, conn_id)))
    finally:
        db.close()

    async def fake_fetch_2(token, property_id, start, end):
        return ROWS_PAGE_1_REVISED
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch_2)
    db = SessionLocal()
    try:
        asyncio.run(ga4.run_sync(db, _load(db, conn_id)))
        rows = db.query(Ga4TrafficSnapshot).filter_by(connection_id=conn_id).all()
        # Still exactly 2 rows (one per dimension combo) — NOT 4. The revised count
        # replaced the old one via the (connection, date, channel, source, medium)
        # unique key, never duplicated.
        assert len(rows) == 2
        chatgpt_row = next(r for r in rows if r.source == "chatgpt.com")
        assert chatgpt_row.sessions == 9
    finally:
        db.close()


def test_run_sync_marks_property_inaccessible_on_403(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_fetch(token, property_id, start, end):
        raise ga4.GoogleApiError("forbidden", status_code=403)
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        with pytest.raises(ga4.GoogleApiError):
            asyncio.run(ga4.run_sync(db, conn))
        assert conn.status == GA4_STATUS_ERROR
        assert conn.last_sync_error_code == "property_inaccessible"
    finally:
        db.close()


def test_run_sync_marks_quota_exceeded_on_429(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_fetch(token, property_id, start, end):
        raise ga4.GoogleApiError("rate limited", status_code=429)
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        with pytest.raises(ga4.GoogleApiError):
            asyncio.run(ga4.run_sync(db, conn))
        assert conn.last_sync_error_code == "quota_exceeded"
    finally:
        db.close()


def test_run_sync_raises_when_credential_is_revoked(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])

    async def fake_refresh(refresh_token):
        return None   # revoked
    monkeypatch.setattr(ga4, "refresh_access_token", fake_refresh)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        with pytest.raises(RuntimeError):
            asyncio.run(ga4.run_sync(db, conn))
        assert conn.status == GA4_STATUS_ERROR
        assert conn.last_sync_error_code == "auth_revoked"
    finally:
        db.close()


def test_run_sync_uses_initial_lookback_for_a_never_synced_connection_and_shorter_lookback_after(monkeypatch):
    # Two distinct orgs: GoogleAnalyticsConnection enforces one row per organization_id.
    client1, body1 = auth_client()
    client2, body2 = auth_client()
    conn_new_id = _connection_id(body1["organization"]["id"], body1["user"]["id"], last_synced_at=None)
    conn_existing_id = _connection_id(body2["organization"]["id"], body2["user"]["id"],
                                      last_synced_at=datetime.utcnow())
    _patch_refresh_ok(monkeypatch)
    seen_ranges = []

    async def fake_fetch(token, property_id, start, end):
        seen_ranges.append((start, end))
        return []
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch)

    db = SessionLocal()
    try:
        asyncio.run(ga4.run_sync(db, _load(db, conn_new_id)))
    finally:
        db.close()
    start1, end1 = seen_ranges[0]
    span1 = (datetime.strptime(end1, "%Y-%m-%d") - datetime.strptime(start1, "%Y-%m-%d")).days
    assert span1 == settings.ga4_initial_sync_lookback_days

    db = SessionLocal()
    try:
        asyncio.run(ga4.run_sync(db, _load(db, conn_existing_id)))
    finally:
        db.close()
    start2, end2 = seen_ranges[1]
    span2 = (datetime.strptime(end2, "%Y-%m-%d") - datetime.strptime(start2, "%Y-%m-%d")).days
    assert span2 == settings.ga4_sync_lookback_days
    assert span2 < span1


# ------------------------------- scheduler + worker dispatch -------------------------------

def test_enqueue_due_ga4_syncs_dedupes_active_jobs():
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        conn.next_sync_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        created = scheduler.enqueue_due_ga4_syncs(db)
        assert created == 1
        # A second pass before the job completes must NOT enqueue a duplicate —
        # enforced by uq_scheduled_jobs_active_per_connection.
        again = scheduler.enqueue_due_ga4_syncs(db)
        assert again == 0
        jobs = db.query(ScheduledJob).filter_by(connection_id=conn_id).all()
        assert len(jobs) == 1
        # Drain the job so it doesn't leak into a later test's claim_job() call (the
        # shared test DB persists across tests in this file).
        jobs[0].status = JOB_COMPLETED
        db.commit()
    finally:
        db.close()


def test_full_claim_and_process_cycle_completes_the_job(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def fake_fetch(token, property_id, start, end):
        return ROWS_PAGE_1
    monkeypatch.setattr(ga4, "fetch_daily_traffic", fake_fetch)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        job = scheduler.enqueue_manual_ga4_sync(db, conn)
        # Claim OUR OWN job by id rather than "whatever claim_job() picks up next" —
        # the shared test-session DB can carry other tests' leftover pending jobs,
        # and this test's point is the ga4_sync dispatch, not claim_job's own
        # ordering (that's covered by test_monitoring.py).
        claimed = db.query(ScheduledJob).filter_by(id=job.id).first()
        claimed.status = JOB_PENDING   # enqueue_manual_ga4_sync already left it pending
        db.commit()
        real_claim = scheduler.claim_job
        # Loop claim_job() until it returns OUR job (draining any stale leftovers first).
        for _ in range(20):
            got = real_claim(db)
            if got is None:
                pytest.fail("claim_job() never returned our job")
            if got.id == job.id:
                claimed = got
                break
            scheduler.complete_job(db, got)   # drain an unrelated leftover job
        asyncio.run(runner.process_job(db, claimed))
        db.refresh(claimed)
        assert claimed.status == JOB_COMPLETED
    finally:
        db.close()


def test_process_job_never_trusts_job_payload_as_proof_of_authorization(monkeypatch):
    """If a job's persisted organization_id somehow disagrees with the connection row
    it points at, the worker must fail closed rather than sync anyway."""
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    db = SessionLocal()
    try:
        job = ScheduledJob(
            connection_id=conn_id, organization_id="a-different-org-entirely",
            kind="ga4_sync", status=JOB_PENDING, attempts=0, max_attempts=3,
            scheduled_for=datetime.utcnow(), run_after=datetime.utcnow(),
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        asyncio.run(runner.process_job(db, job))
        db.refresh(job)
        assert job.status in (JOB_PENDING, JOB_FAILED)   # retried or failed, never completed
        assert job.status != JOB_COMPLETED
    finally:
        db.close()


def test_sync_job_failure_retries_then_fails_permanently(monkeypatch):
    client, body = auth_client()
    conn_id = _connection_id(body["organization"]["id"], body["user"]["id"])
    _patch_refresh_ok(monkeypatch)

    async def always_fails(token, property_id, start, end):
        raise ga4.GoogleApiError("boom", status_code=403)
    monkeypatch.setattr(ga4, "fetch_daily_traffic", always_fails)

    db = SessionLocal()
    try:
        conn = _load(db, conn_id)
        job = scheduler.enqueue_manual_ga4_sync(db, conn)
        job.max_attempts = 1
        db.commit()
        # Mirror the real worker loop: claim_job() increments `attempts` BEFORE
        # process_job runs — calling process_job on an unclaimed job (attempts=0)
        # would never reach max_attempts on the first failure.
        claimed = scheduler.claim_job(db)
        assert claimed.id == job.id
        asyncio.run(runner.process_job(db, claimed))
        db.refresh(claimed)
        assert claimed.status == JOB_FAILED
    finally:
        db.close()
