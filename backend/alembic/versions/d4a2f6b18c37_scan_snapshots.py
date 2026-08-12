"""Change attribution: scan_snapshots.

Additive: a new table holding deterministic per-scan snapshots so score movement can be
explained by diffing consecutive snapshots for the same monitor. Portable JSON payload
(the codebase runs on SQLite in dev/tests; JSONB is not used anywhere). Indexed on
(monitor_id, created_at) for the "previous snapshot for this monitor" lookup.

Revision ID: d4a2f6b18c37
Revises: c1e8b4f7a930
Create Date: 2026-08-12 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a2f6b18c37"
down_revision: Union[str, None] = "c1e8b4f7a930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scan_snapshots_scan_id"), "scan_snapshots", ["scan_id"], unique=False)
    op.create_index(op.f("ix_scan_snapshots_monitor_id"), "scan_snapshots", ["monitor_id"], unique=False)
    op.create_index(op.f("ix_scan_snapshots_organization_id"), "scan_snapshots", ["organization_id"], unique=False)
    op.create_index("ix_scan_snapshots_monitor_created", "scan_snapshots", ["monitor_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_scan_snapshots_monitor_created", table_name="scan_snapshots")
    op.drop_index(op.f("ix_scan_snapshots_organization_id"), table_name="scan_snapshots")
    op.drop_index(op.f("ix_scan_snapshots_monitor_id"), table_name="scan_snapshots")
    op.drop_index(op.f("ix_scan_snapshots_scan_id"), table_name="scan_snapshots")
    op.drop_table("scan_snapshots")
