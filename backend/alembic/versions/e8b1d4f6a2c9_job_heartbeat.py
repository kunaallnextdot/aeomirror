"""Job liveness heartbeat + Google sync failure counter.

Additive only, all nullable:
- scheduled_jobs.heartbeat_at: NULL = a job claimed before heartbeats existed
  (recovered on the older started_at rule).
- google_analytics_connections.sync_failures, search_console_connections.sync_failures:
  consecutive transient sync failures driving retry backoff.

Revision ID: e8b1d4f6a2c9
Revises: d5a9c3e7f1b4
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b1d4f6a2c9"
down_revision: Union[str, None] = "d5a9c3e7f1b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column("google_analytics_connections", sa.Column("sync_failures", sa.Integer(), nullable=True))
    op.add_column("search_console_connections", sa.Column("sync_failures", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("search_console_connections", "sync_failures")
    op.drop_column("google_analytics_connections", "sync_failures")
    op.drop_column("scheduled_jobs", "heartbeat_at")
