"""Per-dataset GA4 sync timestamps.

run_sync fetches three reports; only the by-channel traffic report is required, and
the daily-overview and landing-page reports are best-effort. `last_synced_at` was
stamped on success even when a best-effort report failed, so /trends and
/landing-pages showed a freshness their data didn't have. Two additive, nullable
columns record when each of those reports last actually succeeded.

Backfill: for existing rows, the newest `updated_at` among that dataset's own
snapshot rows. That is a real write time of that dataset, never the global stamp;
connections with no rows stay NULL ("not synced yet") until their next sync.

Revision ID: d3a7f9c2e5b1
Revises: c8e2d5f1a4b7
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a7f9c2e5b1"
down_revision: Union[str, None] = "c8e2d5f1a4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("google_analytics_connections",
                  sa.Column("overview_synced_at", sa.DateTime(), nullable=True))
    op.add_column("google_analytics_connections",
                  sa.Column("landing_pages_synced_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE google_analytics_connections SET overview_synced_at = "
        "(SELECT MAX(o.updated_at) FROM ga4_daily_overview o "
        " WHERE o.connection_id = google_analytics_connections.id)"
    )
    op.execute(
        "UPDATE google_analytics_connections SET landing_pages_synced_at = "
        "(SELECT MAX(l.updated_at) FROM ga4_landing_page_snapshots l "
        " WHERE l.connection_id = google_analytics_connections.id)"
    )


def downgrade() -> None:
    op.drop_column("google_analytics_connections", "landing_pages_synced_at")
    op.drop_column("google_analytics_connections", "overview_synced_at")
