"""Public report share links (report_shares).

Additive: a new table backing shareable read-only report links. The token is stored
(unique) so the owning org can retrieve its own active share URL — a share token grants
only read access to a report the org already owns. A share is valid iff the token
matches AND revoked_at IS NULL AND expires_at > now. Viewer IPs are not stored.

Revision ID: c1e8b4f7a930
Revises: b3e7c1f9a204
Create Date: 2026-08-07 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1e8b4f7a930"
down_revision: Union[str, None] = "b3e7c1f9a204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_shares",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("scan_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_report_shares_token"), "report_shares",
                    ["token"], unique=True)
    op.create_index(op.f("ix_report_shares_scan_id"), "report_shares",
                    ["scan_id"], unique=False)
    op.create_index(op.f("ix_report_shares_organization_id"), "report_shares",
                    ["organization_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_report_shares_organization_id"), table_name="report_shares")
    op.drop_index(op.f("ix_report_shares_scan_id"), table_name="report_shares")
    op.drop_index(op.f("ix_report_shares_token"), table_name="report_shares")
    op.drop_table("report_shares")
