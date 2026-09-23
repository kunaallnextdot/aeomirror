"""AEO Answer Simulator: deterministic/optional-local-LLM answer tracking mode.

Adds the columns and one new table the simulator needs, reusing the existing
Answer Tracking tables (prompt_runs/prompt_results/prompt_result_analysis/
tracked_prompts) rather than a parallel schema — see
app/services/answer_simulator/ and the removed Google Search Console migrations
this chains after (GSC was removed in the same change; e9a1c7b53d20 is the prior
head with no gsc_connections table anymore).

Revision ID: b3c8f1a9d7e2
Revises: e9a1c7b53d20
Create Date: 2026-09-21 00:00:00.000000

Fixed post-creation (still pre-deploy, never applied to Postgres): the two BOOLEAN
NOT NULL columns below (llm_step_requested / llm_step_used) originally used
`server_default=sa.text("0")`, which renders as an UNQUOTED integer literal
(`DEFAULT 0`) — SQLite accepts that (booleans are stored as 0/1 integers there), but
PostgreSQL's BOOLEAN type does not implicitly cast a bare integer default, raising
`DatatypeMismatch`. Switched to `sa.false()`, the same dialect-aware boolean-default
helper already used for every other add-column-with-server_default Boolean elsewhere
in this migration history (see e.g. 7a1c9e4b2f60, a3d9e5c71f04) — renders `DEFAULT
false` on Postgres and the SQLite-equivalent `DEFAULT 0`, correctly on both. Existing
rows are unaffected: this is purely which literal the DDL emits for NEW rows / the
one-time backfill of already-existing rows during the ADD COLUMN, and `false`/`0`
are the same value.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c8f1a9d7e2"
down_revision: Union[str, None] = "e9a1c7b53d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tracked_prompts",
                  sa.Column("source", sa.String(), nullable=False,
                           server_default="manual"))

    op.add_column("prompt_runs",
                  sa.Column("run_mode", sa.String(), nullable=False,
                           server_default="provider_tracking"))
    op.add_column("prompt_runs",
                  sa.Column("llm_step_requested", sa.Boolean(), nullable=False,
                           server_default=sa.false()))

    op.add_column("prompt_results", sa.Column("answerability", sa.String(), nullable=True))
    op.add_column("prompt_results", sa.Column("evidence_coverage_pct", sa.Float(), nullable=True))
    op.add_column("prompt_results",
                  sa.Column("llm_step_used", sa.Boolean(), nullable=False,
                           server_default=sa.false()))

    op.add_column("prompt_result_analysis", sa.Column("missing_information", sa.JSON(), nullable=True))
    op.add_column("prompt_result_analysis", sa.Column("simulator_confidence", sa.String(), nullable=True))

    op.create_table(
        "simulator_evidence",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("result_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("monitor_id", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("matched_terms", sa.JSON(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_simulator_evidence_result", "simulator_evidence", ["result_id"])
    op.create_index(op.f("ix_simulator_evidence_organization_id"), "simulator_evidence", ["organization_id"])
    op.create_index(op.f("ix_simulator_evidence_monitor_id"), "simulator_evidence", ["monitor_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_simulator_evidence_monitor_id"), table_name="simulator_evidence")
    op.drop_index(op.f("ix_simulator_evidence_organization_id"), table_name="simulator_evidence")
    op.drop_index("ix_simulator_evidence_result", table_name="simulator_evidence")
    op.drop_table("simulator_evidence")

    op.drop_column("prompt_result_analysis", "simulator_confidence")
    op.drop_column("prompt_result_analysis", "missing_information")

    op.drop_column("prompt_results", "llm_step_used")
    op.drop_column("prompt_results", "evidence_coverage_pct")
    op.drop_column("prompt_results", "answerability")

    op.drop_column("prompt_runs", "llm_step_requested")
    op.drop_column("prompt_runs", "run_mode")

    op.drop_column("tracked_prompts", "source")
