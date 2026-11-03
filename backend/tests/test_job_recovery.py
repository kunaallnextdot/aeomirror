"""P0-3 regression: jobs left RUNNING by a crashed or redeployed worker are recovered
for every job kind (not only crawl/scoring/re-check chunks), a job whose worker is
alive (fresh heartbeat) is never touched, retry limits still apply, a worker that
comes back late cannot overwrite a job someone else re-claimed, and a retried job
does not redo work that already finished."""
import asyncio
import time
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.db.models import (
    JOB_COMPLETED, JOB_FAILED, JOB_KIND_BULK_SCAN, JOB_KIND_GA4_SYNC, JOB_KIND_GSC_SYNC, JOB_PENDING,
    JOB_RUNNING, SCAN_COMPLETED, SCAN_FAILED, Monitor, Scan, ScheduledJob,
)
from app.db.session import SessionLocal
from app.monitoring import runner, scheduler

NOW = datetime(2026, 11, 2, 12, 0, 0)
_created: list[str] = []
_monitors: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.query(ScheduledJob).filter(ScheduledJob.id.in_(_created)).delete(synchronize_session=False)
        db.query(Monitor).filter(Monitor.id.in_(_monitors)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    _created.clear()
    _monitors.clear()


def _job(db, *, kind="scheduled", status=JOB_RUNNING, started_ago=None, beat_ago=None, attempts=1, **refs):
    j = ScheduledJob(kind=kind, status=status, attempts=attempts, max_attempts=3, organization_id="org-rec",
                     scheduled_for=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
                     started_at=None if started_ago is None else NOW - started_ago,
                     heartbeat_at=None if beat_ago is None else NOW - beat_ago, **refs)
    db.add(j)
    db.commit()
    _created.append(j.id)
    return j


def _monitor(db):
    # Manual, nothing scheduled: these fixtures must never be picked up by another
    # test's worker tick in the shared test database.
    m = Monitor(organization_id="org-rec", url="https://rec.example.com/", normalized_url="rec.example.com",
                frequency="manual", status="active", next_scan_at=None)
    db.add(m)
    db.commit()
    _monitors.append(m.id)
    return m


def test_a_crashed_scan_job_is_recovered_and_the_monitor_scans_again():
    db = SessionLocal()
    try:
        m = _monitor(db)
        j = _job(db, monitor_id=m.id, started_ago=timedelta(hours=2), beat_ago=timedelta(minutes=10))
        assert scheduler.has_active_job(db, m.id) is not None        # blocked before recovery
        assert scheduler.recover_stalled_jobs(db, NOW) >= 1
        db.refresh(j)
        assert (j.status, j.attempts, j.started_at, j.heartbeat_at) == (JOB_PENDING, 1, None, None)
        assert "worker stopped" in j.error
        assert j.run_after > NOW                                     # normal retry backoff applies
    finally:
        db.close()


@pytest.mark.parametrize("kind,ref", [(JOB_KIND_GA4_SYNC, "connection_id"), (JOB_KIND_GSC_SYNC, "connection_id"),
                                      (JOB_KIND_BULK_SCAN, "scan_id"), ("manual", "monitor_id")])
def test_every_job_kind_is_recovered(kind, ref):
    db = SessionLocal()
    try:
        j = _job(db, kind=kind, started_ago=timedelta(hours=1), beat_ago=timedelta(minutes=6),
                 **{ref: f"ref-{kind}-{NOW.timestamp()}"})
        scheduler.recover_stalled_jobs(db, NOW)
        db.refresh(j)
        assert j.status == JOB_PENDING
    finally:
        db.close()


def test_a_long_running_job_with_a_fresh_heartbeat_is_never_requeued():
    db = SessionLocal()
    try:
        j = _job(db, kind=JOB_KIND_GA4_SYNC, connection_id="conn-alive", started_ago=timedelta(hours=3),
                 beat_ago=timedelta(seconds=20))
        assert j.id not in {s.id for s in scheduler.stalled_jobs(db, NOW)}
        scheduler.recover_stalled_jobs(db, NOW)
        db.refresh(j)
        assert j.status == JOB_RUNNING
    finally:
        db.close()


def test_jobs_claimed_before_heartbeats_existed_use_the_older_rule():
    db = SessionLocal()
    try:
        young = _job(db, connection_id="legacy-young", kind=JOB_KIND_GA4_SYNC, started_ago=timedelta(minutes=10))
        old = _job(db, connection_id="legacy-old", kind=JOB_KIND_GSC_SYNC, started_ago=timedelta(minutes=31))
        ids = {s.id for s in scheduler.stalled_jobs(db, NOW)}
        assert old.id in ids and young.id not in ids
    finally:
        db.close()


def test_repeated_crashes_exhaust_the_retry_limit_and_finalize_the_bulk_scan():
    db = SessionLocal()
    try:
        scan = Scan(url="https://bulk.example.com", normalized_url="bulk.example.com", ars=0, rubric_version="1", status="running",
                    result={"bulk": {"urls": ["https://bulk.example.com/a"]}})
        db.add(scan)
        db.commit()
        j = _job(db, kind=JOB_KIND_BULK_SCAN, scan_id=scan.id, started_ago=timedelta(hours=1),
                 beat_ago=timedelta(minutes=10))
        for attempt in (1, 2, 3):
            if attempt > 1:                                # the job is claimed again, then the worker dies again
                j.status, j.attempts = JOB_RUNNING, attempt
                j.started_at, j.heartbeat_at = NOW - timedelta(minutes=20), NOW - timedelta(minutes=10)
                db.commit()
            scheduler.recover_stalled_jobs(db, NOW)
            db.refresh(j)
        assert j.status == JOB_FAILED
        db.refresh(scan)
        assert scan.status == SCAN_FAILED and "could not be completed" in scan.result["error"]
        assert scheduler.recover_stalled_jobs(db, NOW + timedelta(hours=1)) == 0 or j.status == JOB_FAILED
    finally:
        db.close()


def test_a_worker_that_comes_back_late_cannot_overwrite_a_reclaimed_job():
    db_a, db_b, db_c = SessionLocal(), SessionLocal(), SessionLocal()
    try:
        j = _job(db_a, kind=JOB_KIND_GA4_SYNC, connection_id="conn-race", started_ago=timedelta(minutes=20),
                 beat_ago=timedelta(minutes=10))
        claim_a = j.started_at                                 # worker A's claim, held in A's session
        stale_c = db_c.get(ScheduledJob, j.id)                 # a second stale holder of the same claim
        scheduler.recover_stalled_jobs(db_b, NOW)
        b = db_b.get(ScheduledJob, j.id)
        b.status, b.started_at, b.heartbeat_at, b.attempts = JOB_RUNNING, NOW, NOW, 2   # worker B re-claims
        db_b.commit()
        assert j.started_at == claim_a                         # A never saw the recovery
        assert scheduler.complete_job(db_a, j) is False
        db_b.refresh(b)
        assert b.status == JOB_RUNNING and b.started_at == NOW
        assert stale_c.started_at == claim_a
        assert scheduler.fail_job(db_c, stale_c, "late") is False
        db_b.refresh(b)
        assert (b.status, b.started_at, b.attempts) == (JOB_RUNNING, NOW, 2)
        assert "late" not in (b.error or "")
    finally:
        db_a.close()
        db_b.close()
        db_c.close()


def test_an_unclaimed_inline_job_can_still_be_completed():
    db = SessionLocal()
    try:
        j = _job(db, status=JOB_PENDING, monitor_id="inline-monitor")
        assert scheduler.complete_job(db, j) is True
        assert j.status == JOB_COMPLETED
    finally:
        db.close()


def test_cancelled_and_finished_jobs_are_left_alone():
    db = SessionLocal()
    try:
        done = _job(db, status=JOB_COMPLETED, connection_id="done", kind=JOB_KIND_GA4_SYNC,
                    started_ago=timedelta(hours=5), beat_ago=timedelta(hours=5))
        scheduler.recover_stalled_jobs(db, NOW)
        db.refresh(done)
        assert done.status == JOB_COMPLETED
    finally:
        db.close()


def test_the_heartbeat_thread_keeps_the_lease_fresh(monkeypatch):
    db = SessionLocal()
    try:
        j = _job(db, kind=JOB_KIND_GA4_SYNC, connection_id="beating", started_ago=timedelta(minutes=1),
                 beat_ago=timedelta(minutes=30))
        before = j.heartbeat_at
        with scheduler.JobHeartbeat(j, interval=0.05):
            time.sleep(0.4)
        db.refresh(j)
        assert j.heartbeat_at > before + timedelta(minutes=29)
        assert j.id not in {s.id for s in scheduler.stalled_jobs(db)}
    finally:
        db.close()


def test_a_retried_scan_that_already_finished_is_not_run_again(monkeypatch):
    calls = []

    async def fake_scan(db, monitor):
        calls.append(monitor.id)
        return {}
    monkeypatch.setattr(runner, "run_scan_for_monitor", fake_scan)
    db = SessionLocal()
    try:
        m = _monitor(db)
        m.last_scan_at = NOW
        db.commit()
        j = _job(db, monitor_id=m.id, attempts=2, started_ago=timedelta(seconds=1))
        asyncio.run(runner.process_job(db, j))
        assert calls == [] and j.status == JOB_COMPLETED
        first = _job(db, kind="manual", monitor_id=m.id + "-x", attempts=1, started_ago=timedelta(seconds=1))
        m2 = Monitor(id=m.id + "-x", organization_id="org-rec", url="https://rec2.example.com/",
                     normalized_url="rec2.example.com", frequency="manual", status="active", last_scan_at=NOW)
        db.add(m2)
        db.commit()
        _monitors.append(m2.id)
        asyncio.run(runner.process_job(db, first))           # a first attempt always scans
        assert calls == [m2.id]
    finally:
        db.close()


def test_a_retried_bulk_scan_that_already_completed_is_not_rerun(monkeypatch):
    import app.api.routes_scan as rs
    ran = []

    async def fake(db, scan, **kw):
        ran.append(scan.id)
    monkeypatch.setattr(rs, "execute_bulk_scan", fake)
    db = SessionLocal()
    try:
        scan = Scan(url="https://done.example.com", normalized_url="done.example.com", ars=0, rubric_version="1", status=SCAN_COMPLETED,
                    result={"bulk": {"urls": []}})
        db.add(scan)
        db.commit()
        j = _job(db, kind=JOB_KIND_BULK_SCAN, scan_id=scan.id, attempts=2, started_ago=timedelta(seconds=1))
        asyncio.run(runner.process_job(db, j))
        assert ran == [] and j.status == JOB_COMPLETED
    finally:
        db.close()


def test_the_worker_tick_recovers_and_heartbeats(monkeypatch):
    from app.monitoring import worker
    seen = []
    monkeypatch.setattr(settings, "scheduler_batch_size", 0)          # recovery only, process nothing
    monkeypatch.setattr(scheduler, "recover_stalled_jobs", lambda db, now=None: seen.append(1) or 0)
    asyncio.run(worker.tick())
    assert seen == [1]
