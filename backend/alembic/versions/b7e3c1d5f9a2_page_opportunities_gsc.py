"""Page Opportunities (business-value URL scoring) + read-only Google Search Console.

Additive only — no existing column changes and no data is rewritten:
- crawl_urls: + inbound_link_count (nullable; NULL = crawled before it was recorded).
- scheduled_jobs: + page_value_run_id (nullable, for kind="page_value") and its partial
  unique "at most one active chunk per run" index.
- search_console_connections, gsc_page_snapshots (new).
- page_value_runs, page_value_scores (new).

Revision ID: b7e3c1d5f9a2
Revises: a9d4e2f6c1b8
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3c1d5f9a2"
down_revision: Union[str, None] = "a9d4e2f6c1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_WHERE = "status IN ('pending','running') AND page_value_run_id IS NOT NULL"


def upgrade() -> None:
    op.add_column("crawl_urls", sa.Column("inbound_link_count", sa.Integer(), nullable=True))

    op.add_column("scheduled_jobs", sa.Column("page_value_run_id", sa.String(), nullable=True))
    op.create_index("ix_scheduled_jobs_page_value_run_id", "scheduled_jobs", ["page_value_run_id"])
    op.create_index(
        "uq_scheduled_jobs_active_per_page_value_run", "scheduled_jobs", ["page_value_run_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE_WHERE),
        postgresql_where=sa.text(_ACTIVE_WHERE),
    )

    op.create_table(
        "search_console_connections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("site_url", sa.String(), nullable=True),
        sa.Column("permission_level", sa.String(), nullable=True),
        sa.Column("connected_by_user_id", sa.String(), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_sync_status", sa.String(), nullable=True),
        sa.Column("last_sync_error_code", sa.String(), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(), nullable=True),
        sa.Column("window_start", sa.String(), nullable=True),
        sa.Column("window_end", sa.String(), nullable=True),
        sa.Column("rows_fetched", sa.Integer(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_search_console_connections_organization_id", "search_console_connections",
                    ["organization_id"])
    op.create_index("ix_search_console_connections_next_sync_at", "search_console_connections",
                    ["next_sync_at"])
    op.create_index("ix_gsc_conn_org", "search_console_connections", ["organization_id"], unique=True)

    op.create_table(
        "gsc_page_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("page", sa.String(), nullable=False),
        sa.Column("page_key", sa.String(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("position", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_gsc_page_snapshots_organization_id", "gsc_page_snapshots", ["organization_id"])
    op.create_index("ix_gsc_page_snapshots_connection_id", "gsc_page_snapshots", ["connection_id"])
    op.create_index("uq_gsc_page", "gsc_page_snapshots", ["connection_id", "page"], unique=True)
    op.create_index("ix_gsc_page_key", "gsc_page_snapshots", ["connection_id", "page_key"])

    op.create_table(
        "page_value_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("crawl_job_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=True),
        sa.Column("requested_by_user_id", sa.String(), nullable=True),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("total_urls", sa.Integer(), nullable=True),
        sa.Column("processed_urls", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_page_value_runs_organization_id", "page_value_runs", ["organization_id"])
    op.create_index("ix_page_value_runs_monitor_id", "page_value_runs", ["monitor_id"])
    op.create_index("ix_page_value_runs_crawl_job_id", "page_value_runs", ["crawl_job_id"])
    op.create_index("ix_pv_runs_monitor_created", "page_value_runs", ["monitor_id", "created_at"])

    op.create_table(
        "page_value_scores",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("crawl_job_id", sa.String(), nullable=False),
        sa.Column("crawl_url_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("page_title", sa.String(), nullable=True),
        sa.Column("business_page_type", sa.String(), nullable=False),
        sa.Column("classification_confidence", sa.Float(), nullable=False),
        sa.Column("classification_status", sa.String(), nullable=False),
        sa.Column("classification_evidence", sa.JSON(), nullable=True),
        sa.Column("is_commercial", sa.Boolean(), nullable=False),
        sa.Column("ai_sessions", sa.Integer(), nullable=True),
        sa.Column("organic_sessions", sa.Integer(), nullable=True),
        sa.Column("total_sessions", sa.Integer(), nullable=True),
        sa.Column("conversions", sa.Integer(), nullable=True),
        sa.Column("conversion_source", sa.String(), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("revenue_currency", sa.String(), nullable=True),
        sa.Column("gsc_clicks", sa.Integer(), nullable=True),
        sa.Column("gsc_impressions", sa.Integer(), nullable=True),
        sa.Column("gsc_position", sa.Float(), nullable=True),
        sa.Column("inbound_links", sa.Integer(), nullable=True),
        sa.Column("issue_count", sa.Integer(), nullable=True),
        sa.Column("main_issue_code", sa.String(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("measured_signals", sa.Integer(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("priority_rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_page_value_scores_run_id", "page_value_scores", ["run_id"])
    op.create_index("ix_page_value_scores_organization_id", "page_value_scores", ["organization_id"])
    op.create_index("uq_pv_score_run_url", "page_value_scores", ["run_id", "crawl_url_id"], unique=True)
    op.create_index("ix_pv_score_run_score", "page_value_scores", ["run_id", "score"])
    op.create_index("ix_pv_score_run_priority", "page_value_scores", ["run_id", "priority_rank"])
    op.create_index("ix_pv_score_run_type", "page_value_scores", ["run_id", "business_page_type"])


def downgrade() -> None:
    op.drop_table("page_value_scores")
    op.drop_table("page_value_runs")
    op.drop_table("gsc_page_snapshots")
    op.drop_table("search_console_connections")
    op.drop_index("uq_scheduled_jobs_active_per_page_value_run", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_page_value_run_id", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "page_value_run_id")
    op.drop_column("crawl_urls", "inbound_link_count")
