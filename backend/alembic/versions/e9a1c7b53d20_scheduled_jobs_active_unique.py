"""One active scheduled job per monitor (partial unique index).

Root-cause hardening for duplicate monitoring emails: make it impossible for two
concurrent scheduler processes to both enqueue the same monitor. Adds a PARTIAL UNIQUE
index on scheduled_jobs(monitor_id) covering only active (pending|running) rows with a
non-NULL monitor_id, so:
  - a monitor can have at most one active job at a time (enforced atomically in the DB),
  - completed/failed history is untouched (they leave the partial predicate),
  - bulk_scan jobs (monitor_id IS NULL) are unaffected.

Before creating the index we conservatively clean any PRE-EXISTING duplicate active jobs:
per monitor we keep the earliest active job and mark the rest FAILED (never deleted, never
touching completed history) so the unique index can be created without failing. Additive
and reversible.

Revision ID: e9a1c7b53d20
Revises: c1a2b3d4e5f6
Create Date: 2026-09-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9a1c7b53d20"
down_revision: Union[str, None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "uq_scheduled_jobs_active_per_monitor"
_ACTIVE_WHERE = "status IN ('pending','running') AND monitor_id IS NOT NULL"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Resolve pre-existing duplicate ACTIVE jobs so the unique index can be created.
    #    Keep the earliest (created_at, then id) active job per monitor; mark the rest
    #    FAILED. Completed/failed rows are never modified.
    rows = bind.execute(sa.text(
        "SELECT id, monitor_id FROM scheduled_jobs "
        f"WHERE {_ACTIVE_WHERE} ORDER BY monitor_id, created_at, id"
    )).fetchall()
    seen: set = set()
    superseded: list[str] = []
    for job_id, monitor_id in rows:
        if monitor_id in seen:
            superseded.append(job_id)
        else:
            seen.add(monitor_id)
    for job_id in superseded:
        bind.execute(
            sa.text("UPDATE scheduled_jobs SET status='failed', "
                    "error='superseded: duplicate active job (unique-index cleanup)' "
                    "WHERE id = :id"),
            {"id": job_id},
        )

    # 2. Create the partial unique index (same predicate on SQLite + Postgres).
    op.create_index(
        _INDEX, "scheduled_jobs", ["monitor_id"], unique=True,
        sqlite_where=sa.text(_ACTIVE_WHERE),
        postgresql_where=sa.text(_ACTIVE_WHERE),
    )


def downgrade() -> None:
    # Drop only the index; do not attempt to "un-fail" the cleaned-up jobs.
    op.drop_index(_INDEX, table_name="scheduled_jobs")
