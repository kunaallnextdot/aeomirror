"""Background worker (Phase 7) — the single coordinator of background processing.

An asyncio task started in every API process (app.main lifespan) when
SCHEDULER_ENABLED=true. Each process is only a CANDIDATE: a database lease
(app/monitoring/leader.py) elects one leader across all uvicorn workers and all
instances, and only the leader ticks the scheduler: recover stalled jobs, enqueue due
work, drain a bounded batch of claimable jobs one at a time (each capped by
JOB_TIMEOUT_SECONDS), then send due summaries/digests, run answer tracking and
housekeeping. Ticks run on a private event loop in a worker thread, never on the
request loop. `tick()` is a pure function of the DB and is what the tests drive.

The same loop also runs standalone (run_worker.py); it competes for the same lease,
so adding one never creates a second scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import anyio

from app.config import settings
from app.db.session import SessionLocal, mark_background
from app.monitoring import leader, notifications, runner, scheduler

logger = logging.getLogger("aeomirror.worker")

_stop = asyncio.Event()
_stopping = threading.Event()

# Snapshot retention is a slow-moving housekeeping job — run it at most hourly, not on
# every ~minute tick. (None => not run yet this process.)
_SNAPSHOT_PURGE_INTERVAL = 3600.0
_last_snapshot_purge: float | None = None


def _maybe_send_digests(db, keep_going=None) -> int:
    """Best-effort weekly digest dispatch. Never raises into the worker tick."""
    try:
        from app.services.digest import maybe_send_digests
        return maybe_send_digests(db, keep_going=keep_going).get("digests", 0)
    except Exception:   # noqa: BLE001 — email is a side effect; never break the tick
        logger.exception("digest dispatch failed")
        return 0


async def _maybe_run_answer_tracking(db, keep_going=None) -> int:
    """Best-effort scheduled answer-tracking dispatch. Runs AFTER scan processing and
    never raises into the tick — it must never touch or block the scan pipeline."""
    try:
        from app.services.answer_tracking.scheduling import run_scheduled
        return await run_scheduled(db, keep_going=keep_going)
    except Exception:   # noqa: BLE001 — answer tracking is a side feature; never break the tick
        logger.exception("answer-tracking dispatch failed")
        return 0


async def _maybe_run_extractions(db, keep_going=None) -> int:
    """Best-effort Part B analysis: extract meaning from runs whose answer phase is
    terminal but whose extraction is still pending. Never raises into the tick."""
    try:
        from app.services.answer_tracking.extraction import run_pending_extractions
        return await run_pending_extractions(db, keep_going=keep_going)
    except Exception:   # noqa: BLE001 — analysis is a side feature; never break the tick
        logger.exception("answer-tracking extraction failed")
        return 0


def _maybe_purge_snapshots(db) -> int:
    """Best-effort retention cleanup, throttled to ~hourly. Never raises."""
    global _last_snapshot_purge
    now = time.monotonic()
    if _last_snapshot_purge is not None and now - _last_snapshot_purge < _SNAPSHOT_PURGE_INTERVAL:
        return 0
    _last_snapshot_purge = now
    try:
        from app.services.snapshot import purge_old_snapshots
        return purge_old_snapshots(db)
    except Exception:   # noqa: BLE001 — housekeeping must not break the worker
        logger.exception("snapshot purge failed")
        return 0


async def _run_job(db, job) -> None:
    """Run one claimed job under its heartbeat and a wall-clock cap.

    - Timeout: the job is cancelled at its next await and goes through the normal retry
      path (its parent record is finalized once attempts run out).
    - Lost claim: if the heartbeat finds the claim gone (recovered and re-claimed
      elsewhere, or the row no longer running) or cannot be written for long enough
      that recovery may begin, the job is cancelled here so it never runs alongside its
      own retry. Its outcome is not recorded: the claim belongs to someone else now."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(runner.process_job(db, job))
    lost = threading.Event()

    def on_lost():
        lost.set()
        loop.call_soon_threadsafe(task.cancel)
    with scheduler.JobHeartbeat(job, on_lost=on_lost):
        try:
            await asyncio.wait_for(task, timeout=settings.job_timeout_seconds)
        except asyncio.TimeoutError:
            db.rollback()
            logger.warning("job %s (%s) timed out after %ss", job.id, job.kind, settings.job_timeout_seconds)
            scheduler.fail_and_finalize(db, job, f"timed out after {settings.job_timeout_seconds}s")
        except asyncio.CancelledError:
            if not lost.is_set():
                raise
            db.rollback()
            logger.error("job %s (%s) lost its claim and was stopped", job.id, job.kind)


async def _phase(name, holder, go, beat, run, default):
    """Run one side-effecting phase under its database execution lease
    (leader.PhaseLease). Skipped when another process is still executing it; the
    phase's own loop calls `keep_going` before every send/run (fencing + progress)."""
    if not go():
        return default
    with leader.PhaseLease(name, holder=holder) as phase:
        if phase is None:
            return default

        def keep_going():
            beat()
            return phase.held and go()
        result = await run(keep_going)
    beat()
    return result


async def tick(should_continue=None, on_progress=None, holder=None) -> dict:
    """One scheduler pass. Returns counts. Safe to call from tests/scripts.

    `should_continue` is checked before every job claim and every phase: the
    coordinator passes "still leader and not shutting down", so a process that loses
    leadership or is stopping finishes the job in hand and starts nothing new."""
    go = should_continue or (lambda: True)
    beat = on_progress or (lambda: None)
    counts = {"enqueued": 0, "processed": 0, "summaries": {}, "digests": 0, "answer_tracking_runs": 0,
              "answer_tracking_extractions": 0, "snapshots_purged": 0}
    db = SessionLocal()
    try:
        if not go():
            return counts
        scheduler.recover_stalled_jobs(db)
        enqueued = scheduler.enqueue_due(db)
        enqueued += scheduler.enqueue_due_ga4_syncs(db)
        enqueued += scheduler.enqueue_due_gsc_syncs(db)
        enqueued += scheduler.enqueue_due_url_rechecks(db)
        counts["enqueued"] = enqueued
        for _ in range(settings.scheduler_batch_size):
            if not go():
                return counts
            job = scheduler.claim_job(db)
            if not job:
                break
            beat()
            await _run_job(db, job)
            counts["processed"] += 1
            beat()
        # Side-effecting phases: each under its own execution lease, so no two processes
        # ever run the same phase at once (emails are check-then-send per org).
        # Both email phases do blocking sends — offload to threads.
        counts["summaries"] = await _phase(
            "summaries", holder, go, beat,
            lambda k: anyio.to_thread.run_sync(lambda: notifications.maybe_send_summaries(db, keep_going=k)), {})
        counts["digests"] = await _phase(
            "digests", holder, go, beat, lambda k: anyio.to_thread.run_sync(_maybe_send_digests, db, k), 0)
        counts["answer_tracking_runs"] = await _phase(
            "answer_tracking", holder, go, beat, lambda k: _maybe_run_answer_tracking(db, k), 0)
        counts["answer_tracking_extractions"] = await _phase(
            "answer_tracking_extraction", holder, go, beat, lambda k: _maybe_run_extractions(db, k), 0)
        # Idempotent (deletes past-retention rows); runs without a phase lease.
        if go():
            counts["snapshots_purged"] = _maybe_purge_snapshots(db)
        return counts
    finally:
        db.close()


def _tick_in_own_loop(should_continue=None, on_progress=None, holder=None) -> dict:
    """Run one tick on a private event loop in a worker thread. Jobs do synchronous
    DB work, DNS resolution and HTML parsing; on the API's event loop (the in-process
    worker) that stalls every request for the length of a crawl chunk. Every async
    resource a job uses (httpx clients, semaphores) is created inside the job, so
    nothing is shared with the request loop."""
    mark_background()
    return asyncio.run(tick(should_continue, on_progress, holder))


_lease: leader.LeaderLease | None = None


def is_leader() -> bool:
    """Whether THIS process currently coordinates background work."""
    return bool(_lease and _lease.held)


async def run_worker_loop() -> None:
    """Coordinator loop. Every process may run it; only the elected leader ticks.

    Each interval: a non-leader tries to take the lease (cheap single-row UPDATE), the
    leader runs one bounded tick off the request loop. A LeaseKeeper thread renews
    the lease independently of tick length. On stop the current job finishes (or is
    abandoned to heartbeat recovery if shutdown times out) and the lease is released
    so a standby process takes over immediately instead of after the lease expires."""
    global _lease
    mark_background()   # this task's sessions (and its to_thread calls) use the background pool
    _stop.clear()
    _stopping.clear()
    lease = _lease = leader.LeaderLease()
    keeper = leader.LeaseKeeper(lease)
    keeper.start()
    should_continue = lambda: lease.held and not _stopping.is_set()   # noqa: E731
    logger.info("scheduler candidate %s started (interval=%ss)", lease.holder, settings.scheduler_interval_seconds)
    try:
        while not _stop.is_set():
            try:
                if not lease.held and await asyncio.to_thread(lease.try_acquire):
                    logger.info("scheduler leadership acquired by %s", lease.holder)
                if lease.held:
                    keeper.busy(True)
                    try:
                        res = await asyncio.to_thread(_tick_in_own_loop, should_continue, keeper.progress, lease.holder)
                    finally:
                        keeper.busy(False)
                    if res["processed"] or res["enqueued"]:
                        logger.info("worker tick %s", res)
            except Exception:
                logger.exception("worker tick failed")
            try:
                await asyncio.wait_for(_stop.wait(), timeout=settings.scheduler_interval_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        keeper.stop()
        was_leader = lease.held
        lease.release()
        if was_leader:
            logger.info("scheduler leadership released by %s", lease.holder)
        logger.info("monitoring worker stopped")


def stop_worker() -> None:
    _stopping.set()   # read by the tick thread
    _stop.set()


def should_start() -> bool:
    """Start the in-process worker only when enabled and not under tests."""
    return settings.scheduler_enabled and settings.environment.strip().lower() != "test"
