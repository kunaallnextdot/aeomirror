"""Add the missing url_findings.updated_at column.

The UrlFinding model has always declared `updated_at`, but the migration that created
the table (c3d4e5f6a7b8) never added it. Every ORM query on url_findings therefore
failed on a migrated database ("no such column: url_findings.updated_at"), so a Site
Intelligence crawl failed at its analysis step and Action Center's crawl findings could
not load. The test suite builds its schema with create_all() and never saw it.

Additive and nullable; skipped if the column already exists (a database created with
create_all and then stamped).

Revision ID: f7c1d3e9b2a4
Revises: e5b2c7d9a1f3
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c1d3e9b2a4"
down_revision: Union[str, None] = "e5b2c7d9a1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("url_findings", "updated_at"):
        op.add_column("url_findings", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("url_findings", "updated_at"):
        op.drop_column("url_findings", "updated_at")
