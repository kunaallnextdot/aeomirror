"""GA4 AI traffic & conversion intelligence.

- ga4_traffic_snapshots: + campaign (sessionCampaignName), + revenue (totalRevenue).
  Unique key gains campaign. Existing rows keep campaign NULL = "synced before
  campaigns were recorded" (each such row covers every campaign) — never guessed.
- ga4_landing_page_snapshots: + traffic_class, + revenue. Unique key gains
  traffic_class. Existing rows keep NULL = "covers every source".
- ga4_event_snapshots (new): daily event counts by source.
- ga4_event_mappings (new): which events a business counts as conversions.
- google_analytics_connections: + events_synced_at, currency_code, key_event_names,
  data_quality.

Syncs now replace their whole date window, so legacy rows inside the next sync's
window are replaced by the finer-grained ones (no double counting); older days keep
their legacy rows.

Revision ID: a9d4e2f6c1b8
Revises: f7c1d3e9b2a4
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4e2f6c1b8"
down_revision: Union[str, None] = "f7c1d3e9b2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ga4_traffic_snapshots", sa.Column("campaign", sa.String(), nullable=True))
    op.add_column("ga4_traffic_snapshots", sa.Column("revenue", sa.Float(), nullable=True))
    op.drop_index("uq_ga4_snap_dimension", table_name="ga4_traffic_snapshots")
    op.create_index("uq_ga4_snap_dimension_v2", "ga4_traffic_snapshots",
                    ["connection_id", "date", "channel_grouping", "source", "medium", "campaign"],
                    unique=True)

    op.add_column("ga4_landing_page_snapshots", sa.Column("traffic_class", sa.String(), nullable=True))
    op.add_column("ga4_landing_page_snapshots", sa.Column("revenue", sa.Float(), nullable=True))
    op.drop_index("uq_ga4_landing_dimension", table_name="ga4_landing_page_snapshots")
    op.create_index("uq_ga4_landing_dimension_v2", "ga4_landing_page_snapshots",
                    ["connection_id", "date", "landing_page", "traffic_class"], unique=True)

    op.create_table(
        "ga4_event_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("event_name", sa.String(), nullable=False),
        sa.Column("channel_grouping", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("medium", sa.String(), nullable=False),
        sa.Column("traffic_class", sa.String(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("key_events", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_event_snapshots_organization_id", "ga4_event_snapshots", ["organization_id"])
    op.create_index("ix_ga4_event_snapshots_connection_id", "ga4_event_snapshots", ["connection_id"])
    op.create_index("ix_ga4_event_conn_date", "ga4_event_snapshots", ["connection_id", "date"])
    op.create_index("uq_ga4_event_dimension", "ga4_event_snapshots",
                    ["connection_id", "date", "event_name", "channel_grouping", "source", "medium"],
                    unique=True)

    op.create_table(
        "ga4_event_mappings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("property_id", sa.String(), nullable=False),
        sa.Column("event_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("conversion_type", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("counts_revenue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_event_mappings_organization_id", "ga4_event_mappings", ["organization_id"])
    op.create_index("ix_ga4_event_mappings_connection_id", "ga4_event_mappings", ["connection_id"])
    op.create_index("uq_ga4_event_mapping", "ga4_event_mappings", ["connection_id", "event_name"], unique=True)

    op.add_column("google_analytics_connections", sa.Column("events_synced_at", sa.DateTime(), nullable=True))
    op.add_column("google_analytics_connections", sa.Column("currency_code", sa.String(), nullable=True))
    op.add_column("google_analytics_connections", sa.Column("key_event_names", sa.JSON(), nullable=True))
    op.add_column("google_analytics_connections", sa.Column("data_quality", sa.JSON(), nullable=True))


def downgrade() -> None:
    for col in ("data_quality", "key_event_names", "currency_code", "events_synced_at"):
        op.drop_column("google_analytics_connections", col)
    op.drop_table("ga4_event_mappings")
    op.drop_table("ga4_event_snapshots")
    op.drop_index("uq_ga4_landing_dimension_v2", table_name="ga4_landing_page_snapshots")
    op.create_index("uq_ga4_landing_dimension", "ga4_landing_page_snapshots",
                    ["connection_id", "date", "landing_page"], unique=True)
    op.drop_column("ga4_landing_page_snapshots", "revenue")
    op.drop_column("ga4_landing_page_snapshots", "traffic_class")
    op.drop_index("uq_ga4_snap_dimension_v2", table_name="ga4_traffic_snapshots")
    op.create_index("uq_ga4_snap_dimension", "ga4_traffic_snapshots",
                    ["connection_id", "date", "channel_grouping", "source", "medium"], unique=True)
    op.drop_column("ga4_traffic_snapshots", "revenue")
    op.drop_column("ga4_traffic_snapshots", "campaign")
