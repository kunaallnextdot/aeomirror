"""AI Answer Tracking (Part B) — gap-to-action + ordered recommendations.

Additive: prompt_result_analysis.recommended_entities (ordered solutions surfaced for a
query) and the new prompt_gap_analysis table (one gap-to-action per zero-mention prompt
per run). No FK constraints; portable JSON (not JSONB). No backfill.

Revision ID: b8f4a2d6c910
Revises: a3d9e5c71f04
Create Date: 2026-08-14 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8f4a2d6c910"
down_revision: Union[str, None] = "a3d9e5c71f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prompt_result_analysis",
                  sa.Column("recommended_entities", sa.JSON(), nullable=True))

    op.create_table(
        "prompt_gap_analysis",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("has_signal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_gap_analysis_run_id"), "prompt_gap_analysis",
                    ["run_id"], unique=False)
    op.create_index(op.f("ix_prompt_gap_analysis_prompt_id"), "prompt_gap_analysis",
                    ["prompt_id"], unique=False)
    op.create_index(op.f("ix_prompt_gap_analysis_organization_id"), "prompt_gap_analysis",
                    ["organization_id"], unique=False)
    op.create_index("ix_prompt_gap_analysis_run", "prompt_gap_analysis", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prompt_gap_analysis_run", table_name="prompt_gap_analysis")
    op.drop_index(op.f("ix_prompt_gap_analysis_organization_id"), table_name="prompt_gap_analysis")
    op.drop_index(op.f("ix_prompt_gap_analysis_prompt_id"), table_name="prompt_gap_analysis")
    op.drop_index(op.f("ix_prompt_gap_analysis_run_id"), table_name="prompt_gap_analysis")
    op.drop_table("prompt_gap_analysis")

    op.drop_column("prompt_result_analysis", "recommended_entities")
