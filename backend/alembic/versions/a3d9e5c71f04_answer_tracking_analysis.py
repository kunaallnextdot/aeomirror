"""AI Answer Tracking (Part B) — analysis + results.

Additive: brand-identity columns on prompt_sets, an extraction_status column on
prompt_runs, and the new prompt_result_analysis table. No FK constraints; portable
JSON (not JSONB). server_default keeps existing rows valid without a backfill.

Revision ID: a3d9e5c71f04
Revises: f1b8d0c4a7e2
Create Date: 2026-08-13 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d9e5c71f04"
down_revision: Union[str, None] = "f1b8d0c4a7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prompt_sets", sa.Column("brand_name", sa.String(), nullable=True))
    op.add_column("prompt_sets", sa.Column("brand_domain", sa.String(), nullable=True))
    op.add_column("prompt_sets", sa.Column("brand_aliases", sa.JSON(), nullable=True))
    op.add_column("prompt_sets", sa.Column("competitor_domains", sa.JSON(), nullable=True))

    op.add_column("prompt_runs", sa.Column("extraction_status", sa.String(), nullable=False,
                                           server_default="pending"))

    op.create_table(
        "prompt_result_analysis",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("result_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("brand_mentioned", sa.Boolean(), nullable=True),
        sa.Column("mention_context", sa.Text(), nullable=True),
        sa.Column("sentiment", sa.String(), nullable=True),
        sa.Column("brand_urls_cited", sa.JSON(), nullable=True),
        sa.Column("competitors_mentioned", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("extraction_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extraction_model", sa.String(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_result_analysis_result_id"), "prompt_result_analysis",
                    ["result_id"], unique=False)
    op.create_index(op.f("ix_prompt_result_analysis_run_id"), "prompt_result_analysis",
                    ["run_id"], unique=False)
    op.create_index(op.f("ix_prompt_result_analysis_organization_id"), "prompt_result_analysis",
                    ["organization_id"], unique=False)
    op.create_index("ix_prompt_result_analysis_run", "prompt_result_analysis",
                    ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prompt_result_analysis_run", table_name="prompt_result_analysis")
    op.drop_index(op.f("ix_prompt_result_analysis_organization_id"),
                  table_name="prompt_result_analysis")
    op.drop_index(op.f("ix_prompt_result_analysis_run_id"), table_name="prompt_result_analysis")
    op.drop_index(op.f("ix_prompt_result_analysis_result_id"), table_name="prompt_result_analysis")
    op.drop_table("prompt_result_analysis")

    op.drop_column("prompt_runs", "extraction_status")

    op.drop_column("prompt_sets", "competitor_domains")
    op.drop_column("prompt_sets", "brand_aliases")
    op.drop_column("prompt_sets", "brand_domain")
    op.drop_column("prompt_sets", "brand_name")
