"""Free-plan limit changes + usage metering.

- Adds the `usage_events` table (append-only meter for monthly comparison quota).
- Re-syncs the seeded plan rows' `features`/`description` from PLAN_DEFS so existing
  databases reflect the new Free limits (10 scans / 2 monitors / 2 comparisons).
  Pro and the one-time report are unchanged.

Additive and reversible.

Revision ID: f4d9a1c7e620
Revises: e3b8c1d5f9a2
Create Date: 2026-07-27 13:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4d9a1c7e620"
down_revision: Union[str, None] = "e3b8c1d5f9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_plans = sa.table(
    "plans",
    sa.column("code", sa.String),
    sa.column("description", sa.String),
    sa.column("features", sa.JSON),
)


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_usage_events_organization_id", "usage_events", ["organization_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    op.create_index("ix_usage_org_kind_created", "usage_events",
                    ["organization_id", "kind", "created_at"])

    # Re-sync plan marketing copy from code so already-seeded rows show the new Free
    # limits. PLAN_DEFS reads the current settings, so this stays the single source.
    from app.billing.plans import PLAN_DEFS
    bind = op.get_bind()
    for p in PLAN_DEFS:
        bind.execute(
            _plans.update().where(_plans.c.code == p["code"]).values(
                description=p["description"], features=p["features"])
        )


def downgrade() -> None:
    op.drop_index("ix_usage_org_kind_created", table_name="usage_events")
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_organization_id", table_name="usage_events")
    op.drop_table("usage_events")
