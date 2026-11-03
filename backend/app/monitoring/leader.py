"""Leader election: exactly one background coordinator per deployment.

Every process that may run the scheduler (each uvicorn worker of the web service, an
overlapping instance during a zero-downtime deploy, an optional `run_worker.py`) is a
candidate. Leadership is one row in `scheduler_leases`, taken and renewed with
conditional UPDATEs, so the database decides the winner:

- acquire:  UPDATE ... SET holder=me WHERE name=? AND (holder=me OR expires_at < now)
            (or INSERT the row if it does not exist; the primary key rejects a second
            insert). Under PostgreSQL's row lock two candidates can never both match.
- renew:    UPDATE ... WHERE name=? AND holder=me  — 0 rows means someone took over.
- release:  expire our own row on clean shutdown so a standby takes over at once.

A crashed leader stops renewing and is replaced once `expires_at` passes. Locally a
leader treats itself as leader only until (time of its last successful write) + lease
- renew interval, measured on its own monotonic clock from BEFORE the write, so it
steps down before any other candidate can see the lease as expired (up to one renew
interval of wall-clock skew between machines is absorbed).

Leadership only decides who runs scheduler ticks. Job execution is additionally
protected by the queue's own atomic claims and claim-guarded completion
(scheduler.claim_job / _still_ours), so even an overlap could not run a job twice.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models import SchedulerLease
from app.db.session import SessionLocal, mark_background

logger = logging.getLogger("aeomirror.worker")

LEASE_NAME = "scheduler"


def new_holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class LeaderLease:
    def __init__(self, name: str = LEASE_NAME, holder: str | None = None,
                 lease_seconds: float | None = None, renew_seconds: float | None = None,
                 session_factory=SessionLocal):
        self.name = name
        self.holder = holder or new_holder_id()
        self.lease_seconds = lease_seconds if lease_seconds is not None else settings.scheduler_leader_lease_seconds
        self.renew_seconds = renew_seconds if renew_seconds is not None else settings.scheduler_leader_renew_seconds
        self._session_factory = session_factory
        self._valid_until = 0.0   # monotonic; 0 = not leader

    @property
    def held(self) -> bool:
        return time.monotonic() < self._valid_until

    def _granted(self, started: float) -> None:
        self._valid_until = started + self.lease_seconds - self.renew_seconds

    def try_acquire(self, now: datetime | None = None) -> bool:
        """Become leader if the lease is free, expired or already ours."""
        started = time.monotonic()
        now = now or datetime.utcnow()
        expires = now + timedelta(seconds=self.lease_seconds)
        db = self._session_factory()
        try:
            n = (db.query(SchedulerLease)
                 .filter(SchedulerLease.name == self.name,
                         or_(SchedulerLease.holder == self.holder, SchedulerLease.expires_at < now))
                 .update({SchedulerLease.holder: self.holder, SchedulerLease.acquired_at: now,
                          SchedulerLease.renewed_at: now, SchedulerLease.expires_at: expires},
                         synchronize_session=False))
            db.commit()
            if not n:
                if db.get(SchedulerLease, self.name) is not None:
                    return False           # held by a live leader
                db.add(SchedulerLease(name=self.name, holder=self.holder, acquired_at=now,
                                      renewed_at=now, expires_at=expires))
                try:
                    db.commit()
                except IntegrityError:     # another candidate inserted it first
                    db.rollback()
                    return False
            self._granted(started)
            return True
        except Exception:   # noqa: BLE001 — a DB hiccup just means "not leader this round"
            db.rollback()
            logger.warning("scheduler leader election failed (database error)")
            return False
        finally:
            db.close()

    def renew(self, now: datetime | None = None) -> bool:
        """Extend our lease. False when it has been taken over (we step down at once);
        a database error leaves local validity to run out on its own."""
        started = time.monotonic()
        now = now or datetime.utcnow()
        db = self._session_factory()
        try:
            n = (db.query(SchedulerLease)
                 .filter(SchedulerLease.name == self.name, SchedulerLease.holder == self.holder)
                 .update({SchedulerLease.renewed_at: now,
                          SchedulerLease.expires_at: now + timedelta(seconds=self.lease_seconds)},
                         synchronize_session=False))
            db.commit()
        except Exception:   # noqa: BLE001
            db.rollback()
            logger.warning("scheduler leader lease renewal failed (database error)")
            return self.held
        finally:
            db.close()
        if n:
            self._granted(started)
            return True
        if self._valid_until:
            logger.warning("scheduler leadership lost to another process")
        self._valid_until = 0.0
        return False

    def release(self, now: datetime | None = None) -> None:
        """Give leadership up immediately (clean shutdown / watchdog)."""
        self._valid_until = 0.0
        now = now or datetime.utcnow()
        db = self._session_factory()
        try:
            (db.query(SchedulerLease)
             .filter(SchedulerLease.name == self.name, SchedulerLease.holder == self.holder)
             .update({SchedulerLease.expires_at: now - timedelta(seconds=1)}, synchronize_session=False))
            db.commit()
        except Exception:   # noqa: BLE001 — the lease then simply expires
            db.rollback()
            logger.warning("scheduler leader lease release failed; it will expire on its own")
        finally:
            db.close()


class LeaseKeeper:
    """Renews the lease from its own thread (never the API's event loop, never blocked
    by a long job). Watchdog: when the coordinator has made no progress for
    `stuck_after` seconds (a job hung in synchronous code that the per-job timeout
    cannot interrupt) it gives leadership up so another process takes over scheduling;
    the stuck tick sees `lease.held` turn false and claims nothing further."""

    def __init__(self, lease: LeaderLease, stuck_after: float | None = None):
        self.lease = lease
        self.stuck_after = stuck_after if stuck_after is not None else 2 * settings.job_timeout_seconds
        self._progress = time.monotonic()
        self._busy = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scheduler-lease", daemon=True)

    def progress(self) -> None:
        self._progress = time.monotonic()

    def busy(self, value: bool) -> None:
        self._busy = value
        self.progress()

    def stuck(self) -> bool:
        return self._busy and time.monotonic() - self._progress > self.stuck_after

    def _run(self) -> None:
        mark_background()
        while not self._stop.wait(self.lease.renew_seconds):
            if not self.lease.held:
                continue
            if self.stuck():
                logger.error("scheduler made no progress for %ss; giving up leadership", int(self.stuck_after))
                self.lease.release()
                continue
            self.lease.renew()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)


class PhaseLease:
    """A database execution lease for one side-effecting scheduler phase (summaries,
    digests, answer tracking, extraction). Held for as long as the phase runs in a live
    process — renewed by its own thread, independent of scheduler leadership — so a new
    leader can never start a phase that a previous leader is still executing (e.g. after
    the stuck-leader watchdog, or during a deploy overlap). Process death stops the
    renewals and the phase becomes runnable again once the lease expires.

    `held` is the fencing check: phase loops call it before every send/run, and it turns
    false (lease - renew interval after the last successful renewal) before any other
    process could acquire the lease."""

    def __init__(self, phase: str, holder: str | None = None):
        self.lease = LeaderLease(name=f"phase:{phase}", holder=holder)
        self.phase = phase
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"phase-lease-{phase}", daemon=True)

    @property
    def held(self) -> bool:
        return self.lease.held

    def _run(self) -> None:
        mark_background()
        while not self._stop.wait(self.lease.renew_seconds):
            if not self.lease.renew():
                return

    def __enter__(self) -> "PhaseLease | None":
        if not self.lease.try_acquire():
            logger.warning("scheduler phase %s is still running in another process; skipped", self.phase)
            return None
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        if self.lease.held or self.lease._valid_until:
            self.lease.release()
        return False
