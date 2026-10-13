"""Google Analytics 4 integration (P0): OAuth state, connections, traffic snapshots.

Three new tables — oauth_states (short-lived anti-replay tokens for the OAuth
authorization-code flow), google_analytics_connections (one row per org, the
encrypted refresh token + selected GA4 property), ga4_traffic_snapshots (aggregated
daily traffic rows, classified by AI-assistant source) — plus two additive, nullable
columns on the existing scheduled_jobs table (connection_id + its own partial unique
"one active sync job per connection" index) so GA4 syncs reuse the existing
claim-based job queue/worker instead of new infrastructure. No existing table's
existing columns are changed.

Revision ID: a1b2c3d4e5f6
Revises: d7e2b4c91a56
Create Date: 2026-10-13 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d7e2b4c91a56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="google"),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oauth_states_organization_id", "oauth_states", ["organization_id"])
    op.create_index("ix_oauth_states_user_id", "oauth_states", ["user_id"])
    op.create_index("ix_oauth_states_state", "oauth_states", ["state"], unique=True)
    op.create_index("ix_oauth_states_expires", "oauth_states", ["expires_at"])

    op.create_table(
        "google_analytics_connections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("google_account_email", sa.String(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("ga4_property_id", sa.String(), nullable=True),
        sa.Column("ga4_property_name", sa.String(), nullable=True),
        sa.Column("connected_by_user_id", sa.String(), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_status", sa.String(), nullable=True),
        sa.Column("last_sync_error_code", sa.String(), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_property"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_conn_org", "google_analytics_connections",
                     ["organization_id"], unique=True)
    op.create_index("ix_ga4_conn_next_sync_at", "google_analytics_connections",
                     ["next_sync_at"])

    op.create_table(
        "ga4_traffic_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("channel_grouping", sa.String(), nullable=False, server_default="(not set)"),
        sa.Column("source", sa.String(), nullable=False, server_default="(not set)"),
        sa.Column("medium", sa.String(), nullable=False, server_default="(not set)"),
        sa.Column("ai_source_label", sa.String(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ga4_snap_org_date", "ga4_traffic_snapshots",
                     ["organization_id", "date"])
    op.create_index("ix_ga4_snap_connection_id", "ga4_traffic_snapshots", ["connection_id"])
    op.create_index("uq_ga4_snap_dimension", "ga4_traffic_snapshots",
                     ["connection_id", "date", "channel_grouping", "source", "medium"],
                     unique=True)

    # Additive, nullable — existing scheduled_jobs rows are unaffected.
    op.add_column("scheduled_jobs", sa.Column("connection_id", sa.String(), nullable=True))
    op.create_index("ix_jobs_connection_status", "scheduled_jobs",
                     ["connection_id", "status"])
    # Same partial-unique-index pattern as uq_scheduled_jobs_active_per_monitor: at
    # most one active (pending|running) ga4_sync job per connection. Same predicate on
    # SQLite + Postgres so create_all (tests) and this migration (prod) stay in sync.
    op.create_index(
        "uq_scheduled_jobs_active_per_connection", "scheduled_jobs", ["connection_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','running') AND connection_id IS NOT NULL"),
        sqlite_where=sa.text("status IN ('pending','running') AND connection_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_scheduled_jobs_active_per_connection", table_name="scheduled_jobs")
    op.drop_index("ix_jobs_connection_status", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "connection_id")

    op.drop_table("ga4_traffic_snapshots")
    op.drop_table("google_analytics_connections")
    op.drop_table("oauth_states")
