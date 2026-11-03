"""Standalone monitoring worker (Phase 10).

In production the web service runs with SCHEDULER_ENABLED=false and this process
drains the scheduled-scan queue instead, so scans never compete with HTTP workers
and the worker scales independently. The queue is claim-based, so running one (or
several) of these is safe.

On SIGTERM/SIGINT (a deploy or restart) the loop stops claiming new jobs and exits
after the current tick. A job still running when the platform finally kills the
process stops heartbeating and is recovered by the next worker once its lease expires
(scheduler.recover_stalled_jobs).

    python run_worker.py
"""
import asyncio
import logging
import signal

from app.core.observability import configure_logging, init_error_tracking
from app.monitoring.worker import run_worker_loop, stop_worker


async def _main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_worker)
        except (NotImplementedError, RuntimeError):   # e.g. Windows
            pass
    await run_worker_loop()


if __name__ == "__main__":
    configure_logging()
    init_error_tracking()
    logging.getLogger("aeomirror.worker").info("standalone monitoring worker starting")
    asyncio.run(_main())
