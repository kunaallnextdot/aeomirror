"""ORM models for the free-scanner slice. The full product schema is in the PRD."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Scan(Base):
    __tablename__ = "scans"
    id = Column(String, primary_key=True, default=_uuid)
    url = Column(String, nullable=False)
    normalized_url = Column(String, index=True, nullable=False)  # cache key
    ars = Column(Integer, nullable=False)
    rubric_version = Column(String, nullable=False)              # human version string
    # Logical reference to rubric_versions.version this scan was scored with.
    # Nullable so pre-existing rows keep working; backfilled by migration.
    rubric_version_id = Column(String, index=True, nullable=True)
    result = Column(JSON, nullable=False)                        # full report dict
    requester_ip_hash = Column(String, nullable=True)            # hashed, never raw
    lead_email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"
    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False)
    first_scanned_url = Column(String, nullable=True)
    scan_count = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    version = Column(String, primary_key=True)
    family_weights = Column(JSON, nullable=False)
    check_weights = Column(JSON, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    effective_from = Column(DateTime, default=datetime.utcnow)  # created_at semantics
