"""Background worker (Phase 7).

An in-process asyncio task that periodically ticks the scheduler: enqueue due
monitors, drain a bounded batch of claimable jobs, and dispatch due summaries.
`tick()` is a pure function of the DB and is what the tests drive directly.

This runs independently of HTTP requests. Because the queue is claim-based, the
exact same `tick()` logic can later run in a separate worker process (or many) —
just disable the in-process worker (SCHEDULER_ENABLED=false) and run a script that
calls `tick()` in a loop. Nothing else changes.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from app.db.session import SessionLocal
from app.monitoring import notifications, runner, scheduler

logger = logging.getLogger("aeomirror.worker")

_stop = asyncio.Event()

# Snapshot retention is a slow-moving housekeeping job — run it at most hourly, not on
# every ~minute tick. (None => not run yet this process.)
_SNAPSHOT_PURGE_INTERVAL = 3600.0
_last_snapshot_purge: float | None = None


def _maybe_send_digests(db) -> int:
    """Best-effort weekly digest dispatch. Never raises into the worker tick."""
    try:
        from app.services.digest import maybe_send_digests
        return maybe_send_digests(db).get("digests", 0)
    except Exception:   # noqa: BLE001 — email is a side effect; never break the tick
        logger.exception("digest dispatch failed")
        return 0


async def _maybe_run_answer_tracking(db) -> int:
    """Best-effort scheduled answer-tracking dispatch. Runs AFTER scan processing and
    never raises into the tick — it must never touch or block the scan pipeline."""
    try:
        from app.services.answer_tracking.scheduling import run_scheduled
        return await run_scheduled(db)
    except Exception:   # noqa: BLE001 — answer tracking is a side feature; never break the tick
        logger.exception("answer-tracking dispatch failed")
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


async def tick() -> dict:
    """One scheduler pass. Returns counts. Safe to call from tests/scripts."""
    db = SessionLocal()
    try:
        enqueued = scheduler.enqueue_due(db)
        processed = 0
        for _ in range(settings.scheduler_batch_size):
            job = scheduler.claim_job(db)
            if not job:
                break
            await runner.process_job(db, job)
            processed += 1
        summaries = notifications.maybe_send_summaries(db)
        digests = _maybe_send_digests(db)
        answer_runs = await _maybe_run_answer_tracking(db)
        purged = _maybe_purge_snapshots(db)
        return {"enqueued": enqueued, "processed": processed, "summaries": summaries,
                "digests": digests, "answer_tracking_runs": answer_runs,
                "snapshots_purged": purged}
    finally:
        db.close()


async def run_worker_loop() -> None:
    _stop.clear()
    logger.info("monitoring worker started (interval=%ss)", settings.scheduler_interval_seconds)
    while not _stop.is_set():
        try:
            res = await tick()
            if res["processed"] or res["enqueued"]:
                logger.info("worker tick %s", res)
        except Exception:
            logger.exception("worker tick failed")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=settings.scheduler_interval_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("monitoring worker stopped")


def stop_worker() -> None:
    _stop.set()


def should_start() -> bool:
    """Start the in-process worker only when enabled and not under tests."""
    return settings.scheduler_enabled and settings.environment.strip().lower() != "test"
