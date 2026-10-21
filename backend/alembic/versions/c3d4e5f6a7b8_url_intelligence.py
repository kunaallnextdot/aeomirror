"""URL Intelligence Engine + AI Expert Analysis Engine.

Adds `scheduled_jobs.crawl_job_id` (nullable, for kind="url_crawl") plus its partial
unique "at most one active chunk per crawl job" index, and three new tables:
crawl_jobs, crawl_urls, url_findings. No existing table's existing columns change.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_WHERE = "status IN ('pending','running') AND crawl_job_id IS NOT NULL"


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("crawl_job_id", sa.String(), nullable=True))
    op.create_index("ix_scheduled_jobs_crawl_job_id", "scheduled_jobs", ["crawl_job_id"])
    op.create_index(
        "uq_scheduled_jobs_active_per_crawl_job", "scheduled_jobs", ["crawl_job_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE_WHERE),
        postgresql_where=sa.text(_ACTIVE_WHERE),
    )

    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("seed_url", sa.String(), nullable=False),
        sa.Column("normalized_seed_url", sa.String(), nullable=False),
        sa.Column("max_urls", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False, server_default="pending"),
        sa.Column("cursor", sa.JSON(), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("crawled_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analyzed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_calls_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_calls_cached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_tokens_input_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_tokens_output_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_crawl_jobs_org_created", "crawl_jobs", ["organization_id", "created_at"])
    op.create_index("ix_crawl_jobs_monitor", "crawl_jobs", ["monitor_id"])

    op.create_table(
        "crawl_urls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("crawl_job_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("discovery_source", sa.String(), nullable=False),
        sa.Column("discovery_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="discovered"),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("canonical_url", sa.String(), nullable=True),
        sa.Column("page_type", sa.String(), nullable=True),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("content_fingerprint", sa.String(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("analyzed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_crawl_urls_job_status", "crawl_urls", ["crawl_job_id", "status"])
    op.create_index("ix_crawl_urls_job_page_type", "crawl_urls", ["crawl_job_id", "page_type"])
    op.create_index("ix_crawl_urls_job_template", "crawl_urls", ["crawl_job_id", "template_id"])
    op.create_index("uq_crawl_urls_job_normalized", "crawl_urls",
                    ["crawl_job_id", "normalized_key"], unique=True)

    op.create_table(
        "url_findings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("crawl_job_id", sa.String(), nullable=False),
        sa.Column("crawl_url_id", sa.String(), nullable=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("affected_url_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("page_type", sa.String(), nullable=True),
        sa.Column("finding_title", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("evidence_source", sa.String(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("recommended_fix", sa.Text(), nullable=True),
        sa.Column("expected_benefit", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.String(), nullable=False, server_default="insufficient_evidence"),
        sa.Column("evidence_limitations", sa.Text(), nullable=True),
        sa.Column("verification_procedure", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("verification_status", sa.String(), nullable=True),
        sa.Column("content_fingerprint", sa.String(), nullable=True),
        sa.Column("model_used", sa.String(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_detected_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_url_findings_job_priority", "url_findings", ["crawl_job_id", "priority"])
    op.create_index("ix_url_findings_job_status", "url_findings", ["crawl_job_id", "status"])
    op.create_index("ix_url_findings_org_created", "url_findings", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_table("url_findings")
    op.drop_table("crawl_urls")
    op.drop_table("crawl_jobs")
    op.drop_index("uq_scheduled_jobs_active_per_crawl_job", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_crawl_job_id", table_name="scheduled_jobs")
    op.drop_column("scheduled_jobs", "crawl_job_id")
