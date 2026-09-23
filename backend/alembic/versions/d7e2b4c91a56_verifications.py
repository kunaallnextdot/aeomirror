"""Fix Verification V2: persisted verification history.

Adds the smallest additive table needed to make Fix Verification durable — one row
per "Verify" action, referencing the two existing Scan rows it was computed from
(baseline_scan_id, verification_scan_id) rather than duplicating either scan's full
result payload. The derived comparison itself (see app/services/verification.py, a
1:1 port of the existing frontend verification.js engine) is the only new data
stored. No change to any existing table.

Revision ID: d7e2b4c91a56
Revises: c4f9a2b71d38
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e2b4c91a56"
down_revision: Union[str, None] = "c4f9a2b71d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "verifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("baseline_scan_id", sa.String(), nullable=False),
        sa.Column("verification_scan_id", sa.String(), nullable=False),
        sa.Column("signal_id", sa.String(), nullable=False),
        sa.Column("verification_status", sa.String(), nullable=False),
        sa.Column("status_before", sa.String(), nullable=True),
        sa.Column("status_after", sa.String(), nullable=True),
        sa.Column("score_before", sa.Float(), nullable=True),
        sa.Column("score_after", sa.Float(), nullable=True),
        sa.Column("resolved_issues", sa.JSON(), nullable=False),
        sa.Column("remaining_issues", sa.JSON(), nullable=False),
        sa.Column("new_issues", sa.JSON(), nullable=False),
        sa.Column("evidence_changes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_verifications_baseline_signal", "verifications",
                    ["baseline_scan_id", "signal_id", "created_at"])
    op.create_index("ix_verifications_org_created", "verifications",
                    ["organization_id", "created_at"])
    op.create_index(op.f("ix_verifications_organization_id"), "verifications", ["organization_id"])
    op.create_index(op.f("ix_verifications_baseline_scan_id"), "verifications", ["baseline_scan_id"])
    op.create_index(op.f("ix_verifications_verification_scan_id"), "verifications", ["verification_scan_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_verifications_verification_scan_id"), table_name="verifications")
    op.drop_index(op.f("ix_verifications_baseline_scan_id"), table_name="verifications")
    op.drop_index(op.f("ix_verifications_organization_id"), table_name="verifications")
    op.drop_index("ix_verifications_org_created", table_name="verifications")
    op.drop_index("ix_verifications_baseline_signal", table_name="verifications")
    op.drop_table("verifications")
