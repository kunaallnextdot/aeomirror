"""Account-required scanning + new plan limits.

Adds the one-time "bulk trial" flag to organizations. Scan-job / monitor / compare
quotas are counted from existing tables (usage_events for metered actions, monitors
for concurrent count), so no new counter table is needed here.

- organizations.used_bulk_trial — Boolean, default False, server_default "0" so every
  existing org keeps a valid (unused) trial without a data backfill.

Also re-syncs the seeded plan feature text to the new limits (Free = 1 scan job /
month, Pro = 15 scan jobs / month, etc.); the authoritative matrix lives in
app/billing/plans.py and is re-applied idempotently for real deployments.

Revision ID: d5a1c8f37b92
Revises: c7f2b9a41e05
Create Date: 2026-07-28 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a1c8f37b92"
down_revision: Union[str, None] = "c7f2b9a41e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations",
                  sa.Column("used_bulk_trial", sa.Boolean(), nullable=False,
                            server_default="0"))
    _resync_plan_features()


def downgrade() -> None:
    op.drop_column("organizations", "used_bulk_trial")


def _resync_plan_features() -> None:
    """Re-apply the Free/Pro feature lists from the plan definitions so a deployed
    `plans` table matches the new model (Pro is no longer 'unlimited scans')."""
    from app.billing.plans import PLAN_DEFS

    bind = op.get_bind()
    plans = sa.table(
        "plans",
        sa.column("code", sa.String),
        sa.column("features", sa.JSON),
    )
    for p in PLAN_DEFS:
        bind.execute(
            plans.update().where(plans.c.code == p["code"]).values(features=p["features"])
        )
