"""Site Intelligence v2: per-page issue summary, stable finding identity, engine version.

Additive only — nullable columns and two indexes, no data rewritten:
- crawl_urls.page_title / issue_codes / issue_count / issue_severity: the deterministic
  per-page issues, stored at crawl time so the URL inventory can filter and sort
  without loading every row's evidence JSON. Existing rows stay NULL ("not evaluated").
- url_findings.issue_key / evidence_basis: which underlying issue a finding is (stable
  across crawls of a site) and how it is supported. Existing rows stay NULL.
- crawl_jobs.engine_version: only crawls of the same engine version are compared.

Revision ID: e5b2c7d9a1f3
Revises: d3a7f9c2e5b1
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2c7d9a1f3"
down_revision: Union[str, None] = "d3a7f9c2e5b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("crawl_jobs", sa.Column("engine_version", sa.String(), nullable=True))
    op.add_column("crawl_urls", sa.Column("page_title", sa.String(), nullable=True))
    op.add_column("crawl_urls", sa.Column("issue_codes", sa.JSON(), nullable=True))
    op.add_column("crawl_urls", sa.Column("issue_count", sa.Integer(), nullable=True))
    op.add_column("crawl_urls", sa.Column("issue_severity", sa.Integer(), nullable=True))
    op.create_index("ix_crawl_urls_job_issues", "crawl_urls",
                    ["crawl_job_id", "issue_severity", "issue_count"])
    op.add_column("url_findings", sa.Column("issue_key", sa.String(), nullable=True))
    op.add_column("url_findings", sa.Column("evidence_basis", sa.String(), nullable=True))
    op.create_index("ix_url_findings_job_issue_key", "url_findings", ["crawl_job_id", "issue_key"])


def downgrade() -> None:
    op.drop_index("ix_url_findings_job_issue_key", table_name="url_findings")
    op.drop_column("url_findings", "evidence_basis")
    op.drop_column("url_findings", "issue_key")
    op.drop_index("ix_crawl_urls_job_issues", table_name="crawl_urls")
    op.drop_column("crawl_urls", "issue_severity")
    op.drop_column("crawl_urls", "issue_count")
    op.drop_column("crawl_urls", "issue_codes")
    op.drop_column("crawl_urls", "page_title")
    op.drop_column("crawl_jobs", "engine_version")
