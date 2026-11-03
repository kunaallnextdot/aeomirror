"""Single-service background processing: one leader-elected coordinator.

Every API process (uvicorn --workers N) runs a coordinator candidate; a lease row in
`scheduler_leases` elects exactly one leader and only it runs scheduler ticks. These
tests cover the lease itself, the coordinator loop, shutdown, the per-job timeout,
and — with real OS processes — simultaneous start-up, leader crash and takeover, and
concurrent job claims. The multi-process tests run against SQLite always and against
PostgreSQL when AEOMIRROR_TEST_PG_URL points at a disposable database.
"""
import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import (
    CRAWL_FAILED, JOB_COMPLETED, JOB_FAILED, JOB_KIND_BULK_SCAN, JOB_KIND_GA4_SYNC, JOB_KIND_GSC_SYNC,
    JOB_KIND_MANUAL, JOB_KIND_PAGE_VALUE, JOB_KIND_SCHEDULED, JOB_KIND_URL_CRAWL, JOB_KIND_URL_RECHECK,
    JOB_PENDING, JOB_RUNNING, CrawlJob, SchedulerLease, ScheduledJob,
)
from app.db.session import SessionLocal
from app.monitoring import leader, runner, scheduler, worker

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime(2026, 11, 3, 12, 0, 0)
_jobs: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    db = SessionLocal()
    try:
        db.query(ScheduledJob).filter(ScheduledJob.id.in_(_jobs)).delete(synchronize_session=False)
        db.query(SchedulerLease).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    _jobs.clear()


def _lease(name=None, **kw):
    return leader.LeaderLease(name=name or f"t-{uuid.uuid4().hex[:8]}", **kw)


def _row(name):
    db = SessionLocal()
    try:
        return db.get(SchedulerLease, name)
    finally:
        db.close()


def _pending_job(db, kind=JOB_KIND_BULK_SCAN, attempts=0, **refs):
    # Far in the past so it is claimed before anything another test left behind.
    j = ScheduledJob(kind=kind, status=JOB_PENDING, attempts=attempts, max_attempts=3, organization_id="org-lead",
                     scheduled_for=datetime(2000, 1, 1), run_after=None, created_at=T0, updated_at=T0, **refs)
    db.add(j)
    db.commit()
    _jobs.append(j.id)
    return j


# ------------------------------------------------------------------ the lease

def test_only_one_candidate_holds_the_lease():
    a = _lease()
    b = leader.LeaderLease(name=a.name)
    assert a.try_acquire(T0) is True and a.held
    assert b.try_acquire(T0 + timedelta(seconds=1)) is False and not b.held
    assert a.renew(T0 + timedelta(seconds=15)) is True
    assert _row(a.name).holder == a.holder


def test_a_crashed_leader_is_replaced_only_after_its_lease_expires():
    a = _lease(lease_seconds=60, renew_seconds=15)
    b = leader.LeaderLease(name=a.name, lease_seconds=60, renew_seconds=15)
    assert a.try_acquire(T0)
    # a "crashes": never renews again
    assert b.try_acquire(T0 + timedelta(seconds=59)) is False
    assert b.try_acquire(T0 + timedelta(seconds=61)) is True
    # a comes back: its renewal finds the row taken and it steps down at once
    assert a.renew(T0 + timedelta(seconds=62)) is False and not a.held
    assert _row(a.name).holder == b.holder


def test_a_clean_release_hands_over_immediately():
    a = _lease()
    b = leader.LeaderLease(name=a.name)
    assert a.try_acquire()
    a.release()
    assert not a.held and b.try_acquire() is True


def test_a_leader_stops_acting_before_anyone_else_may_take_over(monkeypatch):
    """Local validity = lease - renew interval, from a clock read BEFORE the write."""
    a = _lease(lease_seconds=60, renew_seconds=15)
    base = time.monotonic()
    monkeypatch.setattr(leader.time, "monotonic", lambda: base)
    assert a.try_acquire()
    monkeypatch.setattr(leader.time, "monotonic", lambda: base + 44)
    assert a.held
    monkeypatch.setattr(leader.time, "monotonic", lambda: base + 46)
    assert not a.held          # 14 s before the row's expires_at


def test_a_database_error_never_grants_leadership():
    class Broken:
        def query(self, *_):
            raise RuntimeError("db down")

        def rollback(self):
            pass

        def close(self):
            pass
    a = _lease(session_factory=Broken)
    assert a.try_acquire() is False and not a.held
    a._valid_until = time.monotonic() + 30          # was leader
    assert a.renew() is True                       # transient error: keep the remaining validity…
    a._valid_until = time.monotonic() - 1
    assert a.renew() is False                      # …but never extend it


def test_the_keeper_renews_through_a_long_tick():
    a = _lease(lease_seconds=0.6, renew_seconds=0.1)
    keeper = leader.LeaseKeeper(a, stuck_after=60)
    assert a.try_acquire()
    keeper.start()
    keeper.busy(True)
    time.sleep(1.2)                                # two whole leases
    assert a.held
    keeper.stop()


def test_the_watchdog_gives_leadership_up_when_the_coordinator_is_stuck():
    a = _lease(lease_seconds=3, renew_seconds=0.05)
    b = leader.LeaderLease(name=a.name)
    keeper = leader.LeaseKeeper(a, stuck_after=0.2)
    assert a.try_acquire()
    keeper.start()
    keeper.busy(True)                              # …and never makes progress
    time.sleep(0.6)
    keeper.stop()
    assert not a.held
    assert b.try_acquire() is True                 # released, not left to expire


# ------------------------------------------------------------ the coordinator

def _run_loop_for(seconds, monkeypatch, tick):
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0)
    monkeypatch.setattr(worker, "tick", tick)

    async def main():
        task = asyncio.create_task(worker.run_worker_loop())
        await asyncio.sleep(seconds)
        worker.stop_worker()
        await task
    asyncio.run(main())


def test_a_standby_never_ticks_while_another_process_leads(monkeypatch):
    other = leader.LeaderLease()                   # the real lease name
    assert other.try_acquire()
    calls = []

    async def tick(*_):
        calls.append(1)
        return {"processed": 0, "enqueued": 0}
    _run_loop_for(0.4, monkeypatch, tick)
    assert calls == []
    assert _row(leader.LEASE_NAME).holder == other.holder
    other.release()
    _run_loop_for(0.3, monkeypatch, tick)          # lease free: this process leads
    assert calls


def test_stop_finishes_the_job_in_hand_then_releases_the_lease(monkeypatch):
    state = {}

    async def tick(should_continue, on_progress, *_):
        state["holder"] = worker._lease.holder
        worker.stop_worker()                       # SIGTERM arrives mid-job
        assert not should_continue()               # nothing new will be claimed
        time.sleep(0.2)                            # the job in hand still finishes
        state["finished"] = True
        return {"processed": 1, "enqueued": 0}
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0)
    monkeypatch.setattr(worker, "tick", tick)
    asyncio.run(worker.run_worker_loop())
    row = _row(leader.LEASE_NAME)
    assert state["finished"] and row.holder == state["holder"]
    assert row.expires_at < datetime.utcnow()      # released: a standby can take over now
    assert not worker.is_leader()


def test_a_tick_stops_claiming_once_leadership_is_lost(monkeypatch):
    db = SessionLocal()
    try:
        ids = [_pending_job(db).id for _ in range(3)]
        lead = {"ok": True}
        ran = []

        async def process(db, job):
            ran.append(job.id)
            scheduler.complete_job(db, job)
            lead["ok"] = False                     # leadership lost after the first job
        monkeypatch.setattr(runner, "process_job", process)
        monkeypatch.setattr(settings, "scheduler_batch_size", 5)
        res = asyncio.run(worker.tick(lambda: lead["ok"]))
        assert res["processed"] == 1 and len(ran) == 1
        db.expire_all()
        left = [db.get(ScheduledJob, i).status for i in ids if i not in ran]
        assert left == [JOB_PENDING, JOB_PENDING]  # untouched, for the next leader
    finally:
        db.close()


def test_a_job_past_its_timeout_is_retried(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.2)

    async def hang(db, job):
        await asyncio.sleep(30)
    monkeypatch.setattr(runner, "process_job", hang)
    db = SessionLocal()
    try:
        j = _pending_job(db)
        job = scheduler.claim_job(db)
        assert job.id == j.id
        t = time.perf_counter()
        asyncio.run(worker._run_job(db, job))
        assert time.perf_counter() - t < 2
        db.refresh(job)
        assert job.status == JOB_PENDING and "timed out" in job.error and job.run_after
    finally:
        db.close()


def test_a_timed_out_job_with_no_attempts_left_fails_its_crawl(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 0.2)

    async def hang(db, job):
        await asyncio.sleep(30)
    monkeypatch.setattr(runner, "process_job", hang)
    db = SessionLocal()
    try:
        crawl = CrawlJob(organization_id="org-lead", monitor_id="mon-lead", seed_url="https://lead.example.com/",
                         normalized_seed_url="lead.example.com", max_urls=10, phase="crawling")
        db.add(crawl)
        db.commit()
        j = _pending_job(db, kind=JOB_KIND_URL_CRAWL, attempts=2, crawl_job_id=crawl.id)
        job = scheduler.claim_job(db)
        assert job.id == j.id and job.attempts == 3
        asyncio.run(worker._run_job(db, job))
        db.expire_all()
        assert db.get(ScheduledJob, j.id).status == JOB_FAILED
        assert db.get(CrawlJob, crawl.id).phase == CRAWL_FAILED
    finally:
        db.close()


def _fast_heartbeats(monkeypatch, lease=None):
    monkeypatch.setattr(settings, "job_heartbeat_seconds", 0.05)
    if lease is not None:
        monkeypatch.setattr(settings, "job_lease_seconds", lease)


def test_a_job_whose_claim_was_taken_over_is_stopped_and_records_nothing(monkeypatch):
    """Recovered + re-claimed elsewhere while this worker still runs it: this copy stops
    at once instead of running alongside its retry, and never writes an outcome."""
    _fast_heartbeats(monkeypatch)
    state = {}

    async def slow(db, job):
        other = SessionLocal()
        other.query(ScheduledJob).filter(ScheduledJob.id == job.id).update(
            {ScheduledJob.started_at: datetime.utcnow() + timedelta(seconds=5)})   # another claim
        other.commit()
        other.close()
        for _ in range(100):
            await asyncio.sleep(0.05)
        state["finished"] = True
    monkeypatch.setattr(runner, "process_job", slow)
    db = SessionLocal()
    try:
        _pending_job(db)
        job = scheduler.claim_job(db)
        t = time.perf_counter()
        asyncio.run(worker._run_job(db, job))
        assert time.perf_counter() - t < 2 and "finished" not in state
        db.expire_all()
        row = db.get(ScheduledJob, job.id)
        assert row.status == JOB_RUNNING and row.error is None      # the new claim is untouched
    finally:
        db.close()


def test_a_job_that_cannot_heartbeat_stops_before_recovery_could_begin(monkeypatch):
    _fast_heartbeats(monkeypatch, lease=0.3)                       # gives up after 0.2 s
    monkeypatch.setattr(scheduler, "heartbeat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    state = {}

    async def slow(db, job):
        await asyncio.sleep(5)
        state["finished"] = True
    monkeypatch.setattr(runner, "process_job", slow)
    db = SessionLocal()
    try:
        _pending_job(db)
        job = scheduler.claim_job(db)
        t = time.perf_counter()
        asyncio.run(worker._run_job(db, job))
        assert time.perf_counter() - t < 2 and "finished" not in state
    finally:
        db.close()


def test_a_job_that_completed_its_own_claim_is_never_cancelled(monkeypatch):
    """A crawl chunk completes its job and then enqueues the next chunk; a heartbeat
    landing in between must not cut that tail off."""
    _fast_heartbeats(monkeypatch)
    state = {}

    async def finishing(db, job):
        scheduler.complete_job(db, job)
        await asyncio.sleep(0.4)                                   # several beats see "done"
        state["tail_ran"] = True
    monkeypatch.setattr(runner, "process_job", finishing)
    db = SessionLocal()
    try:
        _pending_job(db)
        job = scheduler.claim_job(db)
        asyncio.run(worker._run_job(db, job))
        assert state.get("tail_ran") is True
    finally:
        db.close()


def test_a_slow_but_alive_job_keeps_its_claim_past_the_job_lease(monkeypatch):
    """No hard cap: a job that is still beating is never recovered, so it can never run
    twice."""
    _fast_heartbeats(monkeypatch, lease=0.3)
    monkeypatch.setattr(settings, "job_timeout_seconds", 30)

    async def slow(db, job):
        await asyncio.sleep(1.0)                                   # > 3 job leases
        assert scheduler.stalled_jobs(db, datetime.utcnow()) == [] or \
            job.id not in {j.id for j in scheduler.stalled_jobs(db, datetime.utcnow())}
        scheduler.complete_job(db, job)
    monkeypatch.setattr(runner, "process_job", slow)
    db = SessionLocal()
    try:
        _pending_job(db)
        job = scheduler.claim_job(db)
        asyncio.run(worker._run_job(db, job))
        db.expire_all()
        assert db.get(ScheduledJob, job.id).status == JOB_COMPLETED
    finally:
        db.close()


# ----------------------------------------------- side-effecting phase leases

def _fast_leases(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_leader_lease_seconds", 0.6)
    monkeypatch.setattr(settings, "scheduler_leader_renew_seconds", 0.1)


def test_a_phase_still_running_in_another_process_is_skipped(monkeypatch):
    calls = []

    async def run(keep_going):
        calls.append(1)
        return 5
    with leader.PhaseLease("digests", holder="old-leader") as held:
        assert held is not None
        got = asyncio.run(worker._phase("digests", "new-leader", lambda: True, lambda: None, run, 0))
    assert got == 0 and calls == []
    assert asyncio.run(worker._phase("digests", "new-leader", lambda: True, lambda: None, run, 0)) == 5


def test_a_long_legitimate_phase_never_overlaps_a_new_leader(monkeypatch):
    """The stuck-leader scenario: the old leader is mid-phase (making progress) when a
    new leader appears. The phase lease is renewed for as long as the phase really runs
    — longer than several leases — so the new leader skips it, then runs it after."""
    _fast_leases(monkeypatch)
    sends, beats = [], []
    started = threading.Event()

    async def long_phase(keep_going):
        started.set()
        for i in range(30):                                        # ~1.5 s = 2.5 leases
            if not keep_going():
                break
            sends.append(("old", i))
            await asyncio.sleep(0.05)
        return len(sends)
    t = threading.Thread(target=lambda: asyncio.run(
        worker._phase("summaries", "old-leader", lambda: True, lambda: beats.append(1), long_phase, 0)))
    t.start()
    started.wait(5)
    skipped = []
    end = time.time() + 1.2
    while time.time() < end:                                       # the new leader keeps trying
        async def new_phase(keep_going):
            skipped.append("ran")
            return 1
        r = asyncio.run(worker._phase("summaries", "new-leader", lambda: True, lambda: None, new_phase, 0))
        assert r == 0
        time.sleep(0.1)
    t.join(5)
    assert len(sends) == 30 and skipped == []                      # the phase was never cut off or doubled
    assert len(beats) >= 30                                        # every send counts as progress (watchdog)

    async def after(keep_going):
        return "ran"
    assert asyncio.run(worker._phase("summaries", "new-leader", lambda: True, lambda: None, after, 0)) == "ran"


def test_a_phase_that_loses_its_lease_stops_sending(monkeypatch):
    _fast_leases(monkeypatch)
    sends = []

    async def phase(keep_going):
        for i in range(40):
            if not keep_going():
                break
            sends.append(i)
            if i == 3:                                             # someone else now owns it
                db = SessionLocal()
                db.query(SchedulerLease).filter(SchedulerLease.name == "phase:digests").update(
                    {SchedulerLease.holder: "thief"})
                db.commit()
                db.close()
            await asyncio.sleep(0.05)
        return len(sends)
    asyncio.run(worker._phase("digests", "me", lambda: True, lambda: None, phase, 0))
    assert 4 <= len(sends) <= 8                                    # stopped within ~one renew interval


def test_phase_loops_check_the_fence_before_every_send():
    from app.services import digest
    from app.services.answer_tracking import extraction, scheduling
    from app.monitoring import notifications
    db = SessionLocal()
    try:
        assert notifications.maybe_send_summaries(db, keep_going=lambda: False) == {"weekly": 0, "monthly": 0}
        assert asyncio.run(scheduling.run_scheduled(db, keep_going=lambda: False)) == 0
        assert asyncio.run(extraction.run_pending_extractions(db, keep_going=lambda: False)) == 0
        assert digest.maybe_send_digests(db, keep_going=lambda: False)["digests"] == 0
    finally:
        db.close()


HANDLERS = {
    JOB_KIND_BULK_SCAN: "_process_bulk_scan_job", JOB_KIND_GA4_SYNC: "_process_ga4_sync_job",
    JOB_KIND_URL_CRAWL: "_process_url_crawl_job", JOB_KIND_GSC_SYNC: "_process_gsc_sync_job",
    JOB_KIND_PAGE_VALUE: "_process_page_value_job", JOB_KIND_URL_RECHECK: "_process_url_recheck_job",
}


@pytest.mark.parametrize("kind", sorted(HANDLERS) + [JOB_KIND_SCHEDULED, JOB_KIND_MANUAL])
def test_every_job_kind_runs_through_the_coordinator(kind, monkeypatch):
    """Crawl, GA4, Search Console, scoring/Page Intelligence, re-checks, bulk and
    monitor scans all dispatch through the same timed, heartbeated coordinator path."""
    from app.db.models import Monitor
    called = []
    if kind in HANDLERS:
        is_async = kind != JOB_KIND_PAGE_VALUE

        async def ahandler(db, job):
            called.append(job.id)
            scheduler.complete_job(db, job)

        def shandler(db, job):
            called.append(job.id)
            scheduler.complete_job(db, job)
        monkeypatch.setattr(runner, HANDLERS[kind], ahandler if is_async else shandler)
    db = SessionLocal()
    try:
        refs = {}
        if kind not in HANDLERS:
            m = Monitor(organization_id="org-lead", url="https://lead.example.com/", normalized_url="lead.example.com",
                        frequency="manual", status="active", next_scan_at=None)
            db.add(m)
            db.commit()
            refs["monitor_id"] = m.id

            async def scan(db, monitor):
                called.append(monitor.id)
            monkeypatch.setattr(runner, "run_scan_for_monitor", scan)
        j = _pending_job(db, kind=kind, **refs)
        job = scheduler.claim_job(db)
        assert job.id == j.id

        asyncio.run(worker._run_job(db, job))
        db.expire_all()
        assert len(called) == 1 and db.get(ScheduledJob, j.id).status == JOB_COMPLETED
        if refs:
            db.query(Monitor).filter(Monitor.id == refs["monitor_id"]).delete()
            db.commit()
    finally:
        db.close()


def test_the_api_stays_responsive_while_the_leader_runs_a_blocking_job(monkeypatch):
    import httpx

    from app.main import app
    db = SessionLocal()
    j = _pending_job(db)
    db.close()
    seen = {}

    async def blocking(db, job):
        if job.id == j.id:
            seen["started"] = time.perf_counter()
            time.sleep(1.0)                        # synchronous parsing / DB work
            seen["ended"] = time.perf_counter()
        scheduler.complete_job(db, job)
    monkeypatch.setattr(runner, "process_job", blocking)
    monkeypatch.setattr(settings, "scheduler_batch_size", 1)
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0)

    async def main():
        task = asyncio.create_task(worker.run_worker_loop())
        latencies = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            deadline = time.perf_counter() + 5
            while "ended" not in seen and time.perf_counter() < deadline:
                t = time.perf_counter()
                r = await c.get("/healthz")
                assert r.status_code == 200
                if "started" in seen:
                    latencies.append(time.perf_counter() - t)
                await asyncio.sleep(0.02)
        worker.stop_worker()
        await task
        return latencies
    latencies = asyncio.run(main())
    assert "ended" in seen and len(latencies) >= 10
    assert max(latencies) < 0.25                   # a request on the job's loop would wait ~1 s


# --------------------------------------------------------- real OS processes

def _db_urls():
    urls = [pytest.param("sqlite", id="sqlite")]
    pg = os.environ.get("AEOMIRROR_TEST_PG_URL")
    urls.append(pytest.param(pg or "", id="postgres",
                             marks=pytest.mark.skipif(not pg, reason="AEOMIRROR_TEST_PG_URL not set")))
    return urls


@pytest.fixture
def fresh_db(request, tmp_path):
    """A brand-new database (all tables) shared by the spawned processes."""
    from app.db.session import Base
    url = request.param if request.param != "sqlite" else f"sqlite:///{tmp_path / 'procs.db'}"
    eng = create_engine(url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield url, eng
    Base.metadata.drop_all(eng)
    eng.dispose()


def _spawn(code, url, *args, extra_env=None):
    env = dict(os.environ, DATABASE_URL=url, PYTHONPATH=BACKEND, **(extra_env or {}))
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), *map(str, args)], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


RACE = """
    import sys, time
    from app.monitoring.leader import LeaderLease
    start = float(sys.argv[1])
    lease = LeaderLease(name="race")
    while time.time() < start:
        pass
    print(lease.try_acquire())
"""


@pytest.mark.parametrize("fresh_db", _db_urls(), indirect=True)
def test_many_processes_starting_at_once_elect_exactly_one_leader(fresh_db):
    url, _ = fresh_db
    for _ in range(3):                             # repeat the race: same answer each time
        start = time.time() + 3
        procs = [_spawn(RACE, url, start) for _ in range(8)]
        outs = [p.communicate(timeout=60) for p in procs]
        results = [o.strip().splitlines()[-1] if o.strip() else f"ERR {e[-300:]}" for o, e in outs]
        assert sorted(results) == ["False"] * 7 + ["True"], results
        eng = create_engine(url)
        with eng.begin() as c:
            c.exec_driver_sql("DELETE FROM scheduler_leases")
        eng.dispose()


COORDINATOR = """
    import asyncio, os, signal, sys, time
    from app.monitoring import worker
    log = sys.argv[1]

    async def tick(should_continue, on_progress, *_):
        with open(log, "a") as f:
            f.write(f"start {os.getpid()} {time.time()}\\n")
        time.sleep(0.3)
        with open(log, "a") as f:
            f.write(f"end {os.getpid()} {time.time()}\\n")
        return {"processed": 0, "enqueued": 0}
    worker.tick = tick

    async def main():
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, worker.stop_worker)
        await worker.run_worker_loop()
    asyncio.run(main())
    print("clean exit")
"""


def _ticks(log):
    try:
        rows = [ln.split() for ln in open(log).read().splitlines() if ln]
    except FileNotFoundError:
        return []
    return [(kind, int(pid), float(t)) for kind, pid, t in rows]


def _wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _assert_never_overlapping(events):
    open_by = {}
    for kind, pid, _ in sorted(events, key=lambda e: e[2]):
        if kind == "start":
            assert not open_by, f"pid {pid} ticked while {list(open_by)} was ticking"
            open_by[pid] = True
        else:
            open_by.pop(pid, None)


@pytest.mark.parametrize("fresh_db", _db_urls(), indirect=True)
def test_leader_crash_takeover_and_graceful_handover_across_processes(fresh_db, tmp_path):
    url, _ = fresh_db
    log = str(tmp_path / "ticks.log")
    env = {"SCHEDULER_LEADER_LEASE_SECONDS": "3", "SCHEDULER_LEADER_RENEW_SECONDS": "1",
           "SCHEDULER_INTERVAL_SECONDS": "1"}
    procs = {p.pid: p for p in (_spawn(COORDINATOR, url, log, extra_env=env) for _ in range(3))}
    try:
        assert _wait_for(lambda: len(_ticks(log)) >= 6, 30), _ticks(log)
        first = {pid for _, pid, _ in _ticks(log)}
        assert len(first) == 1                     # three candidates, one scheduler
        leader_pid = first.pop()

        procs[leader_pid].send_signal(signal.SIGKILL)   # crash: no release
        killed_at = time.time()
        procs.pop(leader_pid).wait(timeout=10)
        assert _wait_for(lambda: any(pid != leader_pid for _, pid, _ in _ticks(log)), 30)
        second_start = min(t for k, pid, t in _ticks(log) if pid != leader_pid and k == "start")
        takeover = second_start - killed_at
        assert takeover < 3 + 1 + 2.5, takeover   # within lease + one interval (+ start-up slack)
        second_pid = next(pid for k, pid, t in _ticks(log) if t == second_start)

        n = len(_ticks(log))
        procs[second_pid].send_signal(signal.SIGTERM)   # graceful: lease released
        stopped_at = time.time()
        out, _ = procs.pop(second_pid).communicate(timeout=20)
        assert "clean exit" in out
        assert _wait_for(lambda: any(pid not in (leader_pid, second_pid) for _, pid, _ in _ticks(log)), 20)
        third_start = min(t for k, pid, t in _ticks(log) if pid not in (leader_pid, second_pid) and k == "start")
        assert third_start - stopped_at < 3, "a released lease must be taken over without waiting for expiry"
        assert len(_ticks(log)) > n
        _assert_never_overlapping([e for e in _ticks(log) if e[1] != leader_pid])
    finally:
        for p in procs.values():
            p.send_signal(signal.SIGTERM)
        for p in procs.values():
            try:
                p.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()


CLAIMER = """
    import sys, time
    from app.db.session import SessionLocal
    from app.monitoring import scheduler
    start = float(sys.argv[1])
    db = SessionLocal()
    while time.time() < start:
        pass
    got = []
    while True:
        job = scheduler.claim_job(db)
        if job is None:
            break
        got.append(job.id)
        scheduler.complete_job(db, job)
    print("claimed:" + ",".join(got))
"""


@pytest.mark.parametrize("fresh_db", _db_urls(), indirect=True)
def test_concurrent_claimers_never_run_a_job_twice(fresh_db):
    url, eng = fresh_db
    S = sessionmaker(bind=eng)
    db = S()
    ids = []
    for i in range(60):
        j = ScheduledJob(kind=JOB_KIND_BULK_SCAN, status=JOB_PENDING, attempts=0, max_attempts=3,
                         scheduled_for=T0 + timedelta(seconds=i), created_at=T0, updated_at=T0)
        db.add(j)
        db.flush()
        ids.append(j.id)
    db.commit()
    db.close()
    start = time.time() + 3
    procs = [_spawn(CLAIMER, url, start) for _ in range(6)]
    outs = [p.communicate(timeout=120) for p in procs]
    lines = [o.strip().splitlines()[-1] if o.strip() else "" for o, _ in outs]
    errors = [e[-300:] for (o, e), ln in zip(outs, lines) if not ln.startswith("claimed:")]
    claimed = [i for ln in lines for i in ln.removeprefix("claimed:").split(",") if i]
    assert not errors, errors
    assert sorted(claimed) == sorted(ids)          # every job exactly once, none twice
    db = S()
    try:
        rows = db.query(ScheduledJob).filter(ScheduledJob.id.in_(ids)).all()
        assert {r.status for r in rows} == {JOB_COMPLETED} and {r.attempts for r in rows} == {1}
    finally:
        db.close()


POOLS = """
    import threading, time
    from sqlalchemy import text
    from app.db import session as s
    held = []

    def hog():
        s.mark_background()
        db = s.SessionLocal()
        db.execute(text("select 1"))
        held.append(db)
    for _ in range(5):                      # 2 + 3 overflow: the whole background pool
        t = threading.Thread(target=hog); t.start(); t.join()
    assert all(d.connection().engine is s.background_engine for d in held)
    t0 = time.perf_counter()
    api = [s.SessionLocal() for _ in range(5)]
    for d in api:
        d.execute(text("select 1"))           # request pool is untouched
    assert all(d.connection().engine is s.engine for d in api)
    print("ok", round(time.perf_counter() - t0, 3), s.engine.pool.checkedout(), s.background_engine.pool.checkedout())
"""


@pytest.mark.skipif(not os.environ.get("AEOMIRROR_TEST_PG_URL"), reason="AEOMIRROR_TEST_PG_URL not set")
def test_background_sessions_never_take_request_pool_connections():
    p = _spawn(POOLS, os.environ["AEOMIRROR_TEST_PG_URL"], extra_env={"DB_POOL_TIMEOUT": "2"})
    out, err = p.communicate(timeout=60)
    assert out.startswith("ok"), err[-800:]
    _, secs, api_out, bg_out = out.split()
    assert float(secs) < 1.0 and (int(api_out), int(bg_out)) == (5, 5)
