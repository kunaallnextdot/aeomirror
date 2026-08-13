"""Scheduled answer-tracking runs.

Honours ANSWER_TRACKING_FREQUENCY. Batched PER ORG with per-set isolation so one org's
(or one set's) failure can never abort the others. Runs entirely off the answer-tracking
tables — it never touches or blocks the scan pipeline.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import RUN_FAILED

from . import service
from .errors import RunRefused
from .runner import execute_run

log = logging.getLogger("app.answer_tracking.scheduling")


async def run_scheduled(db: Session, now: datetime | None = None) -> int:
    """Execute all due prompt sets. Returns the number of runs started. Never raises."""
    now = now or datetime.utcnow()
    due = service.due_prompt_sets(db, now)
    by_org: dict[str, list] = defaultdict(list)
    for ps in due:
        by_org[ps.organization_id].append(ps)

    started = 0
    for org_id, sets in by_org.items():
        for ps in sets:
            try:
                run = service.create_run(db, ps, now=now)     # still subject to cost guards
            except RunRefused:
                continue                                       # guard blocked it; skip quietly
            except Exception:                                  # noqa: BLE001
                log.exception("answer-tracking: scheduling create_run failed org=%s set=%s",
                              org_id, ps.id)
                continue
            try:
                await execute_run(db, run)
                started += 1
            except Exception:                                  # noqa: BLE001 — isolate this set
                log.exception("answer-tracking: scheduled run failed org=%s set=%s",
                              org_id, ps.id)
                try:
                    run.status = RUN_FAILED
                    run.completed_at = now
                    db.commit()
                except Exception:                              # noqa: BLE001
                    db.rollback()
    return started
