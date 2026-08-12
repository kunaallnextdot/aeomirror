"""Weekly digest opt-out fields.

Additive: monitors.digest_enabled (default true) and per-user unsubscribe fields
(users.digest_unsubscribe_token + users.digest_opt_out). No backfill needed — the
server defaults make existing rows digest-enabled / not-opted-out.

Revision ID: e7c3a9f2b641
Revises: d4a2f6b18c37
Create Date: 2026-08-12 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7c3a9f2b641"
down_revision: Union[str, None] = "d4a2f6b18c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column("digest_enabled", sa.Boolean(), nullable=False,
                                        server_default=sa.true()))
    op.add_column("users", sa.Column("digest_unsubscribe_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("digest_opt_out", sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.create_index(op.f("ix_users_digest_unsubscribe_token"), "users",
                    ["digest_unsubscribe_token"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_digest_unsubscribe_token"), table_name="users")
    op.drop_column("users", "digest_opt_out")
    op.drop_column("users", "digest_unsubscribe_token")
    op.drop_column("monitors", "digest_enabled")
