"""Contact & Support: contacts table.

Adds the `contacts` table backing the public contact form and the admin Support
Inbox. Additive and reversible.

Revision ID: e3b8c1d5f9a2
Revises: d1a6f3b90c47
Create Date: 2026-07-27 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3b8c1d5f9a2"
down_revision: Union[str, None] = "d1a6f3b90c47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("ip_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_contacts_email", "contacts", ["email"])
    op.create_index("ix_contacts_status", "contacts", ["status"])
    op.create_index("ix_contacts_created_at", "contacts", ["created_at"])
    op.create_index("ix_contacts_status_created", "contacts", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_contacts_status_created", table_name="contacts")
    op.drop_index("ix_contacts_created_at", table_name="contacts")
    op.drop_index("ix_contacts_status", table_name="contacts")
    op.drop_index("ix_contacts_email", table_name="contacts")
    op.drop_table("contacts")
