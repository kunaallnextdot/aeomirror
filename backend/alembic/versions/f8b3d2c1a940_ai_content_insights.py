"""AI content insights cache (Feature B).

Adds `ai_content_insights` — the per-(scan, page) cache of AI content-quality analysis
(tone / clarity / structure / suggestions / rewrite example). Additive and reversible.

Revision ID: f8b3d2c1a940
Revises: d5a1c8f37b92
Create Date: 2026-07-30 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b3d2c1a940"
down_revision: Union[str, None] = "d5a1c8f37b92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_content_insights",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("page_url", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_content_insights_scan_id", "ai_content_insights", ["scan_id"])
    op.create_index("ix_ai_content_insights_organization_id", "ai_content_insights", ["organization_id"])
    # One cached analysis per (scan, page).
    op.create_index("ix_ai_insights_scan_page", "ai_content_insights",
                    ["scan_id", "page_url"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ai_insights_scan_page", table_name="ai_content_insights")
    op.drop_index("ix_ai_content_insights_organization_id", table_name="ai_content_insights")
    op.drop_index("ix_ai_content_insights_scan_id", table_name="ai_content_insights")
    op.drop_table("ai_content_insights")
