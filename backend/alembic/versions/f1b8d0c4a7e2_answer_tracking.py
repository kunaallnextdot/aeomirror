"""AI Answer Tracking (Part A) — data model.

Additive: four new tables (prompt_sets, tracked_prompts, prompt_runs,
prompt_results) plus their indexes. No FK constraints (codebase convention);
links are plain indexed String columns. Portable JSON (not JSONB) so the schema
works on both Supabase Postgres and the SQLite test/dev database. No backfill.

Revision ID: f1b8d0c4a7e2
Revises: e7c3a9f2b641
Create Date: 2026-08-12 13:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b8d0c4a7e2"
down_revision: Union[str, None] = "e7c3a9f2b641"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_sets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_sets_organization_id"), "prompt_sets",
                    ["organization_id"], unique=False)
    op.create_index(op.f("ix_prompt_sets_monitor_id"), "prompt_sets",
                    ["monitor_id"], unique=False)
    op.create_index("ix_prompt_sets_org", "prompt_sets", ["organization_id"], unique=False)

    op.create_table(
        "tracked_prompts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("prompt_set_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tracked_prompts_prompt_set_id"), "tracked_prompts",
                    ["prompt_set_id"], unique=False)
    op.create_index(op.f("ix_tracked_prompts_organization_id"), "tracked_prompts",
                    ["organization_id"], unique=False)
    op.create_index("ix_tracked_prompts_set", "tracked_prompts", ["prompt_set_id"],
                    unique=False)

    op.create_table(
        "prompt_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("prompt_set_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("total_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_runs_organization_id"), "prompt_runs",
                    ["organization_id"], unique=False)
    op.create_index(op.f("ix_prompt_runs_prompt_set_id"), "prompt_runs",
                    ["prompt_set_id"], unique=False)
    op.create_index("ix_prompt_runs_set_created", "prompt_runs",
                    ["prompt_set_id", "created_at"], unique=False)

    op.create_table(
        "prompt_results",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("is_adaptive_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("search_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prompt_results_run_id"), "prompt_results",
                    ["run_id"], unique=False)
    op.create_index(op.f("ix_prompt_results_prompt_id"), "prompt_results",
                    ["prompt_id"], unique=False)
    op.create_index(op.f("ix_prompt_results_organization_id"), "prompt_results",
                    ["organization_id"], unique=False)
    op.create_index("ix_prompt_results_run_prompt", "prompt_results",
                    ["run_id", "prompt_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prompt_results_run_prompt", table_name="prompt_results")
    op.drop_index(op.f("ix_prompt_results_organization_id"), table_name="prompt_results")
    op.drop_index(op.f("ix_prompt_results_prompt_id"), table_name="prompt_results")
    op.drop_index(op.f("ix_prompt_results_run_id"), table_name="prompt_results")
    op.drop_table("prompt_results")

    op.drop_index("ix_prompt_runs_set_created", table_name="prompt_runs")
    op.drop_index(op.f("ix_prompt_runs_prompt_set_id"), table_name="prompt_runs")
    op.drop_index(op.f("ix_prompt_runs_organization_id"), table_name="prompt_runs")
    op.drop_table("prompt_runs")

    op.drop_index("ix_tracked_prompts_set", table_name="tracked_prompts")
    op.drop_index(op.f("ix_tracked_prompts_organization_id"), table_name="tracked_prompts")
    op.drop_index(op.f("ix_tracked_prompts_prompt_set_id"), table_name="tracked_prompts")
    op.drop_table("tracked_prompts")

    op.drop_index("ix_prompt_sets_org", table_name="prompt_sets")
    op.drop_index(op.f("ix_prompt_sets_monitor_id"), table_name="prompt_sets")
    op.drop_index(op.f("ix_prompt_sets_organization_id"), table_name="prompt_sets")
    op.drop_table("prompt_sets")
