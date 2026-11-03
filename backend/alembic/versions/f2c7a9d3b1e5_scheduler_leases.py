"""Scheduler leader lease.

Additive only: a new table, nothing existing is altered. One row per lease name
("scheduler"); the API process holding it is the single background coordinator.

Revision ID: f2c7a9d3b1e5
Revises: e8b1d4f6a2c9
Create Date: 2026-11-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7a9d3b1e5"
down_revision: Union[str, None] = "e8b1d4f6a2c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduler_leases",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("renewed_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scheduler_leases")
