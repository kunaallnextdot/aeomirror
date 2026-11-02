"""Page Intelligence: per-page deterministic analysis history + on-demand AI suggestions.

Additive only:
- page_value_runs: + analysis_status, analysis_cursor, analysis_total, analyzed_urls,
  analysis_error (nullable; NULL = run predates the analysis stage).
- page_analyses, page_suggestions (new).

Revision ID: c4f8a2d6e1b3
Revises: b7e3c1d5f9a2
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a2d6e1b3"
down_revision: Union[str, None] = "b7e3c1d5f9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("page_value_runs", sa.Column("analysis_status", sa.String(), nullable=True))
    op.add_column("page_value_runs", sa.Column("analysis_cursor", sa.Integer(), nullable=True))
    op.add_column("page_value_runs", sa.Column("analysis_total", sa.Integer(), nullable=True))
    op.add_column("page_value_runs", sa.Column("analyzed_urls", sa.Integer(), nullable=True))
    op.add_column("page_value_runs", sa.Column("analysis_error", sa.String(), nullable=True))

    op.create_table(
        "page_analyses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("crawl_job_id", sa.String(), nullable=False),
        sa.Column("crawl_url_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("page_type", sa.String(), nullable=True),
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("rules_version", sa.String(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("top_issue_code", sa.String(), nullable=True),
        sa.Column("recommendation_codes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_page_analyses_organization_id", "page_analyses", ["organization_id"])
    op.create_index("ix_page_analyses_page", "page_analyses", ["monitor_id", "normalized_key", "created_at"])
    op.create_index("ix_page_analyses_run", "page_analyses", ["run_id"])

    op.create_table(
        "page_suggestions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("crawl_url_id", sa.String(), nullable=False),
        sa.Column("content_fingerprint", sa.String(), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("model_used", sa.String(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("requested_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_page_suggestions_organization_id", "page_suggestions", ["organization_id"])
    op.create_index("ix_page_suggestions_page", "page_suggestions",
                    ["monitor_id", "normalized_key", "content_fingerprint"])


def downgrade() -> None:
    op.drop_table("page_suggestions")
    op.drop_table("page_analyses")
    for col in ("analysis_error", "analyzed_urls", "analysis_total", "analysis_cursor", "analysis_status"):
        op.drop_column("page_value_runs", col)
