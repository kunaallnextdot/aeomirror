"""Phase 6: exportable AI-visibility reports.

Adds `reports` (the latest generated rule-based report per scan) and
`report_exports` (download/export history). Additive and reversible.

Revision ID: 9c2e6b41d7a8
Revises: 7a1c9e4b2f60
Create Date: 2026-07-25 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c2e6b41d7a8"
down_revision: Union[str, None] = "7a1c9e4b2f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("recommendation_count", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_reports_scan_id", "reports", ["scan_id"])
    op.create_index("ix_reports_organization_id", "reports", ["organization_id"])

    op.create_table(
        "report_exports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("report_id", sa.String(), nullable=True),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_report_exports_report_id", "report_exports", ["report_id"])
    op.create_index("ix_report_exports_scan_id", "report_exports", ["scan_id"])
    op.create_index("ix_report_exports_organization_id", "report_exports", ["organization_id"])
    op.create_index("ix_report_exports_user_id", "report_exports", ["user_id"])


def downgrade() -> None:
    op.drop_table("report_exports")
    op.drop_table("reports")
