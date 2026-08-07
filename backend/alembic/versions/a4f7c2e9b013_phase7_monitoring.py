"""Phase 7: continuous monitoring.

Adds monitors, scheduled_jobs, monitor_history, alerts, notification_log.
Additive and reversible.

Revision ID: a4f7c2e9b013
Revises: 9c2e6b41d7a8
Create Date: 2026-07-25 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4f7c2e9b013"
down_revision: Union[str, None] = "9c2e6b41d7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monitors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("normalized_url", sa.String(), nullable=False),
        sa.Column("frequency", sa.String(), nullable=False, server_default="weekly"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("next_scan_at", sa.DateTime(), nullable=True),
        sa.Column("latest_scan_id", sa.String(), nullable=True),
        sa.Column("latest_score", sa.Integer(), nullable=True),
    )
    op.create_index("ix_monitors_organization_id", "monitors", ["organization_id"])
    op.create_index("ix_monitors_user_id", "monitors", ["user_id"])
    op.create_index("ix_monitors_normalized_url", "monitors", ["normalized_url"])
    op.create_index("ix_monitors_org_created", "monitors", ["organization_id", "created_at"])
    op.create_index("ix_monitors_due", "monitors", ["status", "next_scan_at"])

    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("run_after", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_scheduled_jobs_monitor_id", "scheduled_jobs", ["monitor_id"])
    op.create_index("ix_scheduled_jobs_organization_id", "scheduled_jobs", ["organization_id"])
    op.create_index("ix_jobs_claimable", "scheduled_jobs", ["status", "run_after"])
    op.create_index("ix_jobs_monitor_status", "scheduled_jobs", ["monitor_id", "status"])

    op.create_table(
        "monitor_history",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("issue_count", sa.Integer(), nullable=True),
        sa.Column("scores", sa.JSON(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_monitor_history_monitor_id", "monitor_history", ["monitor_id"])
    op.create_index("ix_monitor_history_organization_id", "monitor_history", ["organization_id"])
    op.create_index("ix_history_monitor_created", "monitor_history", ["monitor_id", "created_at"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("scan_id", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_alerts_monitor_id", "alerts", ["monitor_id"])
    op.create_index("ix_alerts_organization_id", "alerts", ["organization_id"])
    op.create_index("ix_alerts_org_created", "alerts", ["organization_id", "created_at"])
    op.create_index("ix_alerts_monitor_created", "alerts", ["monitor_id", "created_at"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("monitor_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False, server_default="email"),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notification_log_organization_id", "notification_log", ["organization_id"])
    op.create_index("ix_notification_log_user_id", "notification_log", ["user_id"])
    op.create_index("ix_notiflog_org_created", "notification_log", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("alerts")
    op.drop_table("monitor_history")
    op.drop_table("scheduled_jobs")
    op.drop_table("monitors")
