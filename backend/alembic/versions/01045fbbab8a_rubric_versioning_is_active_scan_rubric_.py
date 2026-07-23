"""rubric versioning: is_active + scan.rubric_version_id

Adds an is_active flag to rubric_versions and a rubric_version_id reference to
scans, seeds the current code rubric as the active version (with identical
weights, so scores are unchanged), and backfills existing scans.

Revision ID: 01045fbbab8a
Revises: 130edb950479
Create Date: 2026-07-23 13:12:22.015459
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON, Boolean, DateTime, String, column, table

# revision identifiers, used by Alembic.
revision: str = '01045fbbab8a'
down_revision: Union[str, None] = '130edb950479'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New columns/indexes. server_default so existing rows get a value on Postgres.
    op.add_column('rubric_versions',
                  sa.Column('is_active', sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.create_index(op.f('ix_rubric_versions_is_active'), 'rubric_versions',
                    ['is_active'], unique=False)
    op.add_column('scans', sa.Column('rubric_version_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_scans_rubric_version_id'), 'scans',
                    ['rubric_version_id'], unique=False)

    # Seed the current code rubric as the active version (idempotent). Weights are
    # the same values the engine used before, so historical scores stay comparable.
    from app.scanner.rubric import CHECK_WEIGHTS, FAMILY_WEIGHTS, RUBRIC_VERSION
    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT 1 FROM rubric_versions WHERE version = :v"),
        {"v": RUBRIC_VERSION},
    ).first()
    rv = table(
        "rubric_versions",
        column("version", String), column("family_weights", JSON),
        column("check_weights", JSON), column("is_active", Boolean),
        column("effective_from", DateTime),
    )
    if not exists:
        op.bulk_insert(rv, [{
            "version": RUBRIC_VERSION,
            "family_weights": FAMILY_WEIGHTS,
            "check_weights": CHECK_WEIGHTS,
            "is_active": True,
            "effective_from": datetime.utcnow(),
        }])
    else:
        conn.execute(
            sa.text("UPDATE rubric_versions SET is_active = :t WHERE version = :v"),
            {"t": True, "v": RUBRIC_VERSION},
        )

    # Backfill existing scans so historical rows reference their rubric version.
    conn.execute(sa.text(
        "UPDATE scans SET rubric_version_id = rubric_version "
        "WHERE rubric_version_id IS NULL"))


def downgrade() -> None:
    op.drop_index(op.f('ix_scans_rubric_version_id'), table_name='scans')
    op.drop_column('scans', 'rubric_version_id')
    op.drop_index(op.f('ix_rubric_versions_is_active'), table_name='rubric_versions')
    op.drop_column('rubric_versions', 'is_active')
    # The seeded rubric_versions row is intentionally left in place (harmless once
    # is_active is dropped); re-upgrade re-activates it via the existence check.
