"""GA4 Website Intelligence dashboard: additive metric columns on
ga4_traffic_snapshots, a key_events_configured flag on
google_analytics_connections, and two new sibling tables
(ga4_daily_overview, ga4_landing_page_snapshots).

All new columns on the existing ga4_traffic_snapshots table are additive and
non-breaking (NOT NULL with a server_default of 0, so every existing row
back-fills cleanly). ga4_daily_overview stores GA4's `totalUsers` at a
date-only grain — safe to read one day at a time, never safe to sum across
rows (see Ga4DailyOverview's docstring in app/db/models.py) — it is a new
table, not a new column on ga4_traffic_snapshots, specifically so that trap
cannot occur by accident. ga4_landing_page_snapshots is a new sibling table
at a (date, landing_page) grain; no existing table's existing columns are
changed.

Revision ID: b4f7e1a9c2d3
Revises: c3d4e5f6a7b8
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4f7e1a9c2d3"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "google_analytics_connections",
        sa.Column("key_events_configured", sa.Boolean(), nullable=True),
    )

    op.add_column("ga4_traffic_snapshots",
                   sa.Column("new_users", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ga4_traffic_snapshots",
                   sa.Column("engaged_sessions", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ga4_traffic_snapshots",
                   sa.Column("views", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ga4_traffic_snapshots",
                   sa.Column("engagement_duration_sec", sa.Float(), nullable=False, server_default="0"))
    op.add_column("ga4_traffic_snapshots",
                   sa.Column("key_events", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "ga4_daily_overview",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("total_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engaged_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement_duration_sec", sa.Float(), nullable=False, server_default="0"),
        sa.Column("key_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_daily_overview_org_date", "ga4_daily_overview",
                     ["organization_id", "date"])
    op.create_index("uq_ga4_daily_overview_dimension", "ga4_daily_overview",
                     ["connection_id", "date"], unique=True)

    op.create_table(
        "ga4_landing_page_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("landing_page", sa.String(), nullable=False, server_default="(not set)"),
        sa.Column("sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engaged_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagement_duration_sec", sa.Float(), nullable=False, server_default="0"),
        sa.Column("key_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_landing_org_date", "ga4_landing_page_snapshots",
                     ["organization_id", "date"])
    op.create_index("uq_ga4_landing_dimension", "ga4_landing_page_snapshots",
                     ["connection_id", "date", "landing_page"], unique=True)


def downgrade() -> None:
    op.drop_table("ga4_landing_page_snapshots")
    op.drop_table("ga4_daily_overview")

    op.drop_column("ga4_traffic_snapshots", "key_events")
    op.drop_column("ga4_traffic_snapshots", "engagement_duration_sec")
    op.drop_column("ga4_traffic_snapshots", "views")
    op.drop_column("ga4_traffic_snapshots", "engaged_sessions")
    op.drop_column("ga4_traffic_snapshots", "new_users")

    op.drop_column("google_analytics_connections", "key_events_configured")
