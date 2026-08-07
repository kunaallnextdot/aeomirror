"""dashboard: composite index on (requester_ip_hash, created_at)

Speeds up the Phase 4 IP-scoped, newest-first scan/dashboard queries. Additive,
no data change.

Revision ID: b2f4a7c9d1e3
Revises: 01045fbbab8a
Create Date: 2026-07-23 16:20:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b2f4a7c9d1e3"
down_revision: Union[str, None] = "01045fbbab8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_scans_requester_created", "scans",
                    ["requester_ip_hash", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_scans_requester_created", table_name="scans")
