"""URL-wise optimization: fix lifecycle, verification timeline, performance snapshots.

Additive only:
- scheduled_jobs: + recheck_monitor_id (nullable, for kind="url_recheck") and its
  partial unique index (at most one active re-check batch per website).
- url_actions, url_action_events, url_performance_snapshots (new).

Revision ID: d5a9c3e7f1b4
Revises: c4f8a2d6e1b3
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a9c3e7f1b4"
down_revision: Union[str, None] = "c4f8a2d6e1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_WHERE = "status IN ('pending','running') AND recheck_monitor_id IS NOT NULL"


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("recheck_monitor_id", sa.String(), nullable=True))
    op.create_index("ix_scheduled_jobs_recheck_monitor_id", "scheduled_jobs", ["recheck_monitor_id"])
    op.create_index(
        "uq_scheduled_jobs_active_per_recheck_monitor", "scheduled_jobs", ["recheck_monitor_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE_WHERE),
        postgresql_where=sa.text(_ACTIVE_WHERE),
    )

    op.create_table(
        "url_actions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("issue", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("page_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ignored_reason", sa.String(), nullable=True),
        sa.Column("baseline_crawl_job_id", sa.String(), nullable=True),
        sa.Column("baseline_evidence", sa.JSON(), nullable=True),
        sa.Column("baseline_observed", sa.String(), nullable=True),
        sa.Column("rules_version", sa.String(), nullable=True),
        sa.Column("engine_version", sa.String(), nullable=True),
        sa.Column("work_started_at", sa.DateTime(), nullable=True),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_result", sa.String(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_check_method", sa.String(), nullable=True),
        sa.Column("check_error", sa.String(), nullable=True),
        sa.Column("unable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_fixed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_fixed_fingerprint", sa.String(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.Column("check_priority", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recheck_requested_at", sa.DateTime(), nullable=True),
        sa.Column("next_snapshot_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_url_actions_organization_id", "url_actions", ["organization_id"])
    op.create_index("uq_url_actions_page_code", "url_actions", ["monitor_id", "normalized_key", "code"], unique=True)
    op.create_index("ix_url_actions_monitor_status", "url_actions", ["monitor_id", "status"])
    op.create_index("ix_url_actions_due", "url_actions", ["next_check_at"])
    op.create_index("ix_url_actions_snapshot_due", "url_actions", ["next_snapshot_at"])

    op.create_table(
        "url_action_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=True),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_url_action_events_organization_id", "url_action_events", ["organization_id"])
    op.create_index("ix_url_action_events_action", "url_action_events", ["action_id", "created_at"])
    op.create_index("ix_url_action_events_page", "url_action_events", ["monitor_id", "normalized_key", "created_at"])
    op.create_index("ix_url_action_events_org_kind", "url_action_events", ["organization_id", "kind", "created_at"])

    op.create_table(
        "url_performance_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("window_start", sa.String(), nullable=True),
        sa.Column("window_end", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_url_performance_snapshots_organization_id", "url_performance_snapshots", ["organization_id"])
    op.create_index("uq_url_perf_snapshot", "url_performance_snapshots", ["action_id", "period", "source"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_url_perf_snapshot", table_name="url_performance_snapshots")
    op.drop_index("ix_url_performance_snapshots_organization_id", table_name="url_performance_snapshots")
    op.drop_table("url_performance_snapshots")
    for ix in ("ix_url_action_events_org_kind", "ix_url_action_events_page", "ix_url_action_events_action",
               "ix_url_action_events_organization_id"):
        op.drop_index(ix, table_name="url_action_events")
    op.drop_table("url_action_events")
    for ix in ("ix_url_actions_snapshot_due", "ix_url_actions_due", "ix_url_actions_monitor_status",
               "uq_url_actions_page_code", "ix_url_actions_organization_id"):
        op.drop_index(ix, table_name="url_actions")
    op.drop_table("url_actions")
    op.drop_index("uq_scheduled_jobs_active_per_recheck_monitor", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_recheck_monitor_id", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "recheck_monitor_id")
