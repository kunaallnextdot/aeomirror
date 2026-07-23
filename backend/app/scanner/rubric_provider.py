"""Active-rubric provider.

Loads the active row from rubric_versions and returns a Rubric. Result is cached
in-process for a short TTL so scans do not hit the DB every time, and a new active
version takes effect within the TTL without a restart. Falls back to the code
default if the DB has no active rubric or is unavailable — the scanner must never
fail because of rubric loading.

Status multipliers are structural (pass/warn/fail) and stay in code; only the
family/check weights are versioned in the database.
"""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.orm import Session

from app.scanner.rubric import Rubric, default_rubric

logger = logging.getLogger("aeomirror.rubric")

_TTL_SECONDS = 60.0
_lock = threading.Lock()
_cache: dict = {"rubric": None, "ts": 0.0}


def get_active_rubric(db: Session, *, now: float | None = None) -> Rubric:
    now = time.time() if now is None else now
    with _lock:
        cached = _cache["rubric"]
        if cached is not None and (now - _cache["ts"]) < _TTL_SECONDS:
            return cached

    rubric = _load_from_db(db)

    with _lock:
        _cache["rubric"] = rubric
        _cache["ts"] = now
    return rubric


def _load_from_db(db: Session) -> Rubric:
    try:
        from app.db.models import RubricVersion
        row = (db.query(RubricVersion)
               .filter(RubricVersion.is_active.is_(True))
               .order_by(RubricVersion.effective_from.desc())
               .first())
        if row and row.family_weights and row.check_weights:
            base = default_rubric()
            return Rubric(
                version=row.version,
                family_weights=dict(row.family_weights),
                check_weights=dict(row.check_weights),
                status_multiplier=base.status_multiplier,
            )
        logger.info("No active rubric in DB; using code default %s",
                    default_rubric().version)
    except Exception as e:
        logger.warning("Rubric load failed (%s); using code default", type(e).__name__)
    return default_rubric()


def clear_cache() -> None:
    """Drop the cached rubric (used by tests and after activating a new version)."""
    with _lock:
        _cache["rubric"] = None
        _cache["ts"] = 0.0
