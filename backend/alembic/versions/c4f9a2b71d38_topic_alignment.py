"""AEO Answer Simulator: off-topic/low-relevance topic alignment fields.

Adds two nullable columns to prompt_results for the deterministic off-topic guard
(see app/services/answer_simulator/topic_alignment.py) — no new table; follows the
same additive-column pattern the prior answer-simulator migration already used for
answerability/evidence_coverage_pct on this same table.

Revision ID: c4f9a2b71d38
Revises: b3c8f1a9d7e2
Create Date: 2026-09-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f9a2b71d38"
down_revision: Union[str, None] = "b3c8f1a9d7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prompt_results", sa.Column("topic_alignment_score", sa.Float(), nullable=True))
    op.add_column("prompt_results", sa.Column("question_token_coverage", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("prompt_results", "question_token_coverage")
    op.drop_column("prompt_results", "topic_alignment_score")
