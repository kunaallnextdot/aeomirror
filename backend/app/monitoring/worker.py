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

from app.config import settings
from app.db.session import SessionLocal
from app.monitoring import notifications, runner, scheduler

logger = logging.getLogger("aeomirror.worker")

_stop = asyncio.Event()


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
        return {"enqueued": enqueued, "processed": processed, "summaries": summaries}
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
