"""Standalone monitoring worker (Phase 10).

In production the web service runs with SCHEDULER_ENABLED=false and this process
drains the scheduled-scan queue instead, so scans never compete with HTTP workers
and the worker scales independently. The queue is claim-based, so running one (or
several) of these is safe.

    python run_worker.py
"""
import asyncio
import logging

from app.core.observability import configure_logging, init_error_tracking
from app.monitoring.worker import run_worker_loop

if __name__ == "__main__":
    configure_logging()
    init_error_tracking()
    logging.getLogger("aeomirror.worker").info("standalone monitoring worker starting")
    asyncio.run(run_worker_loop())
