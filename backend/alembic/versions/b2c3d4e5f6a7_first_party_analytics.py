"""First-party website analytics (AI Business Impact).

Three new tables (analytics_sessions, analytics_pageviews, analytics_conversions) for
the GA4-independent tracker, plus one additive nullable column on the existing
monitors table (tracking_api_key_hash, for the server-to-server purchase/booking
confirm endpoint). No existing table's existing columns change.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column("tracking_api_key_hash", sa.String(), nullable=True))

    op.create_table(
        "analytics_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("visitor_id", sa.String(), nullable=True),
        sa.Column("is_returning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("landing_url", sa.String(), nullable=False),
        sa.Column("referrer_host", sa.String(), nullable=True),
        sa.Column("utm_source", sa.String(), nullable=True),
        sa.Column("utm_medium", sa.String(), nullable=True),
        sa.Column("utm_campaign", sa.String(), nullable=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("page_view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_analytics_sessions_organization_id", "analytics_sessions", ["organization_id"])
    op.create_index("ix_analytics_sessions_monitor_id", "analytics_sessions", ["monitor_id"])
    op.create_index("ix_analytics_sessions_started_at", "analytics_sessions", ["started_at"])
    op.create_index("uq_analytics_sessions_monitor_session", "analytics_sessions",
                     ["monitor_id", "session_id"], unique=True)
    op.create_index("ix_analytics_sessions_org_started", "analytics_sessions",
                     ["organization_id", "started_at"])
    op.create_index("ix_analytics_sessions_monitor_started", "analytics_sessions",
                     ["monitor_id", "started_at"])
    op.create_index("ix_analytics_sessions_visitor", "analytics_sessions",
                     ["monitor_id", "visitor_id"])

    op.create_table(
        "analytics_pageviews",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("analytics_session_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_analytics_pv_organization_id", "analytics_pageviews", ["organization_id"])
    op.create_index("ix_analytics_pv_monitor_id", "analytics_pageviews", ["monitor_id"])
    op.create_index("ix_analytics_pv_monitor_created", "analytics_pageviews",
                     ["monitor_id", "created_at"])
    op.create_index("ix_analytics_pv_session", "analytics_pageviews", ["analytics_session_id"])
    op.create_index("uq_analytics_pv_event", "analytics_pageviews",
                     ["monitor_id", "event_id"], unique=True)

    op.create_table(
        "analytics_conversions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("analytics_session_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("event_name", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("value_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_analytics_conv_organization_id", "analytics_conversions", ["organization_id"])
    op.create_index("ix_analytics_conv_monitor_id", "analytics_conversions", ["monitor_id"])
    op.create_index("ix_analytics_conv_monitor_created", "analytics_conversions",
                     ["monitor_id", "created_at"])
    op.create_index("uq_analytics_conv_external", "analytics_conversions",
                     ["monitor_id", "event_type", "external_id"], unique=True)


def downgrade() -> None:
    op.drop_table("analytics_conversions")
    op.drop_table("analytics_pageviews")
    op.drop_table("analytics_sessions")
    op.drop_column("monitors", "tracking_api_key_hash")
