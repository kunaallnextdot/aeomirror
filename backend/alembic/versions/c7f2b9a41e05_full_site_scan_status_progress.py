"""Full-site scan: scan lifecycle + progress, and site-scan jobs.

Turns the multi-page scan into a background job with progress tracking:
- scans.status  — lifecycle ("pending" | "running" | "completed" | "failed").
  server_default "completed" so every existing row (all single-page, already done)
  stays valid without a data backfill.
- scans.progress — JSON crawl progress for a running site scan (nullable).
- scheduled_jobs.scan_id — reference for kind="site_scan" jobs (nullable).
- scheduled_jobs.monitor_id — relaxed to nullable so a site_scan job (which has no
  monitor) can be enqueued on the same claim-based queue.

Additive and reversible. The monitor_id relaxation uses batch mode so it works on
SQLite (dev) as well as Postgres (prod).

Revision ID: c7f2b9a41e05
Revises: f4d9a1c7e620
Create Date: 2026-07-27 17:20:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f2b9a41e05"
down_revision: Union[str, None] = "f4d9a1c7e620"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- scans: lifecycle + progress ---
    op.add_column("scans", sa.Column("status", sa.String(), nullable=False,
                                     server_default="completed"))
    op.add_column("scans", sa.Column("progress", sa.JSON(), nullable=True))

    # --- scheduled_jobs: carry a scan_id and allow monitor-less jobs ---
    op.add_column("scheduled_jobs", sa.Column("scan_id", sa.String(), nullable=True))
    op.create_index("ix_scheduled_jobs_scan_id", "scheduled_jobs", ["scan_id"])
    with op.batch_alter_table("scheduled_jobs") as batch_op:
        batch_op.alter_column("monitor_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("scheduled_jobs") as batch_op:
        batch_op.alter_column("monitor_id", existing_type=sa.String(), nullable=False)
    op.drop_index("ix_scheduled_jobs_scan_id", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "scan_id")

    op.drop_column("scans", "progress")
    op.drop_column("scans", "status")
