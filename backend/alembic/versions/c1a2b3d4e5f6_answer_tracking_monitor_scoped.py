"""AI Answer Tracking — prompts belong to a monitor (site), not a free-floating set.

Additive + data migration (no data loss). No FK constraints; portable JSON (not JSONB);
correlated-subquery UPDATEs so the backfill runs on both SQLite (dev) and Postgres (prod).

New columns:
  - monitors.brand_name / brand_domain / brand_aliases / competitor_domains
    (brand identity describes the SITE — moved off the prompt set)
  - prompt_runs / prompt_results / prompt_result_analysis / prompt_gap_analysis .monitor_id
    (every run/result/analysis row is attributable to a site)

Backfill:
  - each monitor's brand fields are seeded from its linked prompt set(s) — most recent
    non-null value wins per field (never reset)
  - monitor_id flows prompt_sets -> runs -> results/analysis/gap

The prompt_sets table is RETAINED (least-disruptive): the whole execution/analysis pipeline
keys off prompt_set_id, so we keep one hidden set per monitor rather than rewrite it. Sets
simply disappear from the UI and the user's model.

Prompt sets with NO monitor_id cannot be attributed to a site; they are LEFT UNTOUCHED (never
deleted or reassigned) and reported to stdout so an operator can resolve them by hand.

Revision ID: c1a2b3d4e5f6
Revises: b8f4a2d6c910
Create Date: 2026-08-14 16:40:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, None] = "b8f4a2d6c910"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. brand identity onto the monitor ---
    op.add_column("monitors", sa.Column("brand_name", sa.String(), nullable=True))
    op.add_column("monitors", sa.Column("brand_domain", sa.String(), nullable=True))
    op.add_column("monitors", sa.Column("brand_aliases", sa.JSON(), nullable=True))
    op.add_column("monitors", sa.Column("competitor_domains", sa.JSON(), nullable=True))

    # --- 2. monitor_id on every run/result/analysis row ---
    for table in ("prompt_runs", "prompt_results", "prompt_result_analysis", "prompt_gap_analysis"):
        op.add_column(table, sa.Column("monitor_id", sa.String(), nullable=True))
        op.create_index(op.f(f"ix_{table}_monitor_id"), table, ["monitor_id"], unique=False)

    bind = op.get_bind()
    backfill(bind)              # steps 3+4 — factored so tests exercise the real SQL
    monitorless_sets(bind)      # step 5 — report monitorless sets; never delete/guess


def backfill(bind) -> None:
    """Seed monitor brand fields from linked prompt sets (most recent non-null wins per field),
    then flow monitor_id from sets -> runs -> results/analysis/gap. Correlated subqueries so it
    runs identically on SQLite (dev/tests) and Postgres (prod). Idempotent (WHERE ... IS NULL)."""
    for field in ("brand_name", "brand_domain", "brand_aliases", "competitor_domains"):
        bind.execute(sa.text(
            f"""
            UPDATE monitors
               SET {field} = (
                    SELECT ps.{field} FROM prompt_sets ps
                     WHERE ps.monitor_id = monitors.id AND ps.{field} IS NOT NULL
                     ORDER BY ps.created_at DESC LIMIT 1)
             WHERE {field} IS NULL
               AND EXISTS (SELECT 1 FROM prompt_sets ps2
                            WHERE ps2.monitor_id = monitors.id AND ps2.{field} IS NOT NULL)
            """
        ))
    bind.execute(sa.text(
        """
        UPDATE prompt_runs
           SET monitor_id = (SELECT ps.monitor_id FROM prompt_sets ps
                              WHERE ps.id = prompt_runs.prompt_set_id)
         WHERE monitor_id IS NULL
        """
    ))
    for table in ("prompt_results", "prompt_result_analysis", "prompt_gap_analysis"):
        bind.execute(sa.text(
            f"""
            UPDATE {table}
               SET monitor_id = (SELECT r.monitor_id FROM prompt_runs r
                                  WHERE r.id = {table}.run_id)
             WHERE monitor_id IS NULL
            """
        ))


def monitorless_sets(bind) -> list:
    """Prompt sets with no monitor — they CANNOT be attributed to a site. Left untouched
    (never deleted/reassigned) and printed so an operator resolves them by hand."""
    orphans = bind.execute(sa.text(
        """
        SELECT ps.id, ps.name, ps.organization_id,
               (SELECT COUNT(*) FROM tracked_prompts tp WHERE tp.prompt_set_id = ps.id) AS prompts
          FROM prompt_sets ps
         WHERE ps.monitor_id IS NULL
        """
    )).fetchall()
    if orphans:
        print("\n[answer-tracking migration] MONITORLESS PROMPT SETS — resolve manually "
              "(left untouched, NOT deleted):")
        for row in orphans:
            print(f"  set_id={row[0]} name={row[1]!r} org={row[2]} prompts={row[3]}")
        print("[answer-tracking migration] end of monitorless prompt sets.\n")
    return orphans


def downgrade() -> None:
    for table in ("prompt_gap_analysis", "prompt_result_analysis", "prompt_results", "prompt_runs"):
        op.drop_index(op.f(f"ix_{table}_monitor_id"), table_name=table)
        op.drop_column(table, "monitor_id")
    op.drop_column("monitors", "competitor_domains")
    op.drop_column("monitors", "brand_aliases")
    op.drop_column("monitors", "brand_domain")
    op.drop_column("monitors", "brand_name")
