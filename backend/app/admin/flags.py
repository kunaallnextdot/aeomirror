"""Feature flags (Phase 8). DB-backed with code defaults, so a flag works before
it is ever written. `is_enabled` is a cheap primary-key lookup; the admin API sets
them. Used to gate monitoring, PDF export, email, alerts and experimental features."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import FeatureFlag

# name -> default. Experimental is off by default; everything else on.
DEFAULT_FLAGS: dict[str, bool] = {
    "monitoring": True,
    "pdf_export": True,
    "email": True,
    "alerts": True,
    "experimental": False,
}


def is_enabled(db: Session, name: str) -> bool:
    row = db.get(FeatureFlag, name)
    if row is not None:
        return bool(row.enabled)
    return DEFAULT_FLAGS.get(name, True)


def all_flags(db: Session) -> dict[str, bool]:
    merged = dict(DEFAULT_FLAGS)
    for row in db.query(FeatureFlag).all():
        merged[row.name] = bool(row.enabled)
    return merged


def set_flag(db: Session, name: str, enabled: bool, actor_id: str | None = None) -> None:
    from datetime import datetime
    row = db.get(FeatureFlag, name)
    if row:
        row.enabled = bool(enabled)
        row.updated_by = actor_id
        row.updated_at = datetime.utcnow()
    else:
        db.add(FeatureFlag(name=name, enabled=bool(enabled), updated_by=actor_id))
    db.commit()


def flag_enabled_safe(name: str) -> bool:
    """Convenience for call sites that only want the default when no DB is handy."""
    return DEFAULT_FLAGS.get(name, True)
