"""Explicit GA4 property -> website (Monitor) link.

One additive, nullable column on google_analytics_connections. The GA4 Admin API's
accountSummaries response carries no website hostname, so which of an org's sites a
property measures can only come from an explicit user choice. NULL (every existing
row) means "not linked", and per-website views show no GA4 data until it is set.

Revision ID: c8e2d5f1a4b7
Revises: b4f7e1a9c2d3
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2d5f1a4b7"
down_revision: Union[str, None] = "b4f7e1a9c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("google_analytics_connections",
                  sa.Column("linked_monitor_id", sa.String(), nullable=True))
    op.create_index("ix_google_analytics_connections_linked_monitor_id",
                    "google_analytics_connections", ["linked_monitor_id"])


def downgrade() -> None:
    op.drop_index("ix_google_analytics_connections_linked_monitor_id",
                  table_name="google_analytics_connections")
    op.drop_column("google_analytics_connections", "linked_monitor_id")
