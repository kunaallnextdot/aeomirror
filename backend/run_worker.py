"""Standalone monitoring worker (optional).

Not needed in the default deployment: the web service runs the coordinator in-process
(SCHEDULER_ENABLED=true) and a database lease elects one leader across its uvicorn
workers (app/monitoring/leader.py). This script runs the same coordinator loop and
competes for the same lease, so starting it never creates a second scheduler — it is
a standby that takes over only when no API process holds the lease.

On SIGTERM/SIGINT (a deploy or restart) the loop stops claiming new jobs, finishes the
current one and releases the lease. A job still running when the platform finally
kills the process stops heartbeating and is recovered by the next leader once its
lease expires (scheduler.recover_stalled_jobs).

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
