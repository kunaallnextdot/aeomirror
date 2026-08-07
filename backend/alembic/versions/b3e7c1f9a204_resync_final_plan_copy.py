"""Re-sync plan marketing copy to the final plan model.

Data-only: re-applies each plan's `features` (and name/description) from the
authoritative definitions in app/billing/plans.py so a deployed `plans` table matches
the final copy (Free = 1 scan job / month + scores-only bulk trial; Pro = 15 scan jobs
/ month with full per-page detail, AI-written reports and AI Content Insights; $9 =
one scan's full report + exports + AI narrative). Idempotent; safe to re-run.

Revision ID: b3e7c1f9a204
Revises: f8b3d2c1a940
Create Date: 2026-07-30 14:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7c1f9a204"
down_revision: Union[str, None] = "f8b3d2c1a940"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _resync() -> None:
    from app.billing.plans import PLAN_DEFS

    bind = op.get_bind()
    plans = sa.table(
        "plans",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("features", sa.JSON),
    )
    for p in PLAN_DEFS:
        bind.execute(
            plans.update().where(plans.c.code == p["code"]).values(
                name=p["name"], description=p["description"], features=p["features"],
            )
        )


def upgrade() -> None:
    _resync()


def downgrade() -> None:
    # Copy-only migration; nothing to reverse (older copy is not restored).
    pass
