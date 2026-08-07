"""Unify the headline score onto the honest 10-signal aggregate.

The product exposes one "AI Readiness Score". It must equal the weighted aggregate
of the 10 signals the UI actually displays (robots, sitemap, metadata, schema,
content, links, performance, accessibility, freshness, ai_readiness) so the headline
always reflects the checks — including failures — shown beneath it.

Historically the dashboard/report read the 10-signal `overall_score` while the
homepage gauge showed the legacy 6-family `ars`, so the same scan showed two numbers
(and the headline could sit far above clearly-failing signals). Going forward the
scanner and every surface use the 10-signal `overall_score`; this migration
recomputes it from each stored scan's `sections` so existing rows are consistent.

Data-only and idempotent. Pre-Phase-3 rows (no `sections`) are left unchanged.

Revision ID: d1a6f3b90c47
Revises: c9e5a71f4b28
Create Date: 2026-07-27 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1a6f3b90c47"
down_revision: Union[str, None] = "c9e5a71f4b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_scans = sa.table(
    "scans",
    sa.column("id", sa.String),
    sa.column("result", sa.JSON),
)


def _aggregate(sections: list) -> int | None:
    """Weighted mean of signal scores, clamped to 0-100 — mirrors
    app.scanner.signals.aggregate.run_signals / base.clamp."""
    if not sections:
        return None
    total_weight = sum(s.get("weight", 0) for s in sections) or 1
    raw = sum(s.get("score", 0) * s.get("weight", 0) for s in sections) / total_weight
    return int(max(0, min(100, round(raw))))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(_scans.c.id, _scans.c.result)).fetchall()
    for r in rows:
        result = dict(r.result or {})
        sections = result.get("sections")
        if not sections:
            continue
        recomputed = _aggregate(sections)
        if recomputed is None or result.get("overall_score") == recomputed:
            continue
        result["overall_score"] = recomputed
        bind.execute(
            sa.update(_scans).where(_scans.c.id == r.id).values(result=result)
        )


def downgrade() -> None:
    # One-way data backfill; the overall_score is derived from sections that remain
    # in the row, so there is nothing lost to restore. No-op (schema is unchanged).
    pass
