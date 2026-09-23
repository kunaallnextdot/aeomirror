"""Regression test for the AI Visibility Report 500 (ref 66f7ca4c2cd8406c).

ROOT CAUSE: `GscConnection` was added to `app/db/models.py` with a real Alembic
migration (`a1b2c3d4e5f6_gsc_connections.py`), and `reports/service.get_or_build_report`
— the ONE canonical report-cache path every report endpoint shares, including
`/reports/{id}` and `/reports/{id}/ai-visibility` — was wired to query it on every
report build. That is correct, additive design. The regression was operational: the
local dev database had never had `alembic upgrade head` run against it after the
migration was written, so `gsc_connections` genuinely did not exist there, and the
very first `SELECT` against it raised `sqlite3.OperationalError: no such table:
gsc_connections`, which `get_or_build_report()` did not (and should not) swallow —
it propagated to FastAPI's generic 500 handler, which is exactly the opaque
"unexpected error" surfaced in the browser.

The existing test suite could not have caught this: `tests/conftest.py` builds its
schema with `Base.metadata.create_all()`, which always includes every ORM model
regardless of whether a migration for it was ever written OR ever applied anywhere
real. That is the correct choice for fast, isolated unit tests, but it means the test
suite is structurally blind to model/migration drift.

This test closes that blind spot for the class of bug that IS mechanically
detectable: a table exists on `Base.metadata` (an ORM model was defined) but running
the actual Alembic migration chain from scratch does not produce it (a migration was
never written, or the chain is broken/out of order). It builds a real database purely
via `alembic upgrade head` — the same mechanism `render.yaml`'s `preDeployCommand`
uses in production — and asserts every ORM-registered table is present. It will NOT
catch "a migration exists but a specific deployment forgot to run it" (that is an
operational/deploy-process fact, not a code fact, and no unit test can observe another
machine's database state) — but it does guarantee this repository can never again ship
a new persisted model without a matching migration, which is the actionable, testable
half of this incident.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db.session import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tables that are legitimately alembic-managed metadata, not app models — excluded
# from the parity check (alembic creates this one itself, before any app revision runs).
_ALEMBIC_INTERNAL_TABLES = {"alembic_version"}


def test_migrating_from_scratch_creates_every_orm_registered_table():
    """`alembic upgrade head` against a brand-new database must produce a table for
    every model registered on `Base.metadata` — i.e. no model was ever added without a
    migration. This is the exact mechanism that failed in production use for
    `gsc_connections` (present on `Base.metadata`, and a migration for it DID exist in
    the repo — this test would have passed for that specific gap; it exists to catch
    the same class of bug the next time a model ships with no migration at all, which
    `Base.metadata.create_all()`-based unit tests can never detect)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "migration_parity_check.db"
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}",
              "ENVIRONMENT": "test", "REDIS_URL": ""}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        engine = sa.create_engine(f"sqlite:///{db_path}")
        try:
            migrated_tables = set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    orm_tables = set(Base.metadata.tables.keys())
    missing = orm_tables - migrated_tables - _ALEMBIC_INTERNAL_TABLES
    assert not missing, (
        f"Model(s) registered in app/db/models.py have no corresponding migration, "
        f"so a real (migration-managed) database will never get these tables: {sorted(missing)}. "
        f"This is exactly the class of bug behind the AI Visibility 500 regression "
        f"(ref 66f7ca4c2cd8406c) — add an Alembic migration for each of them."
    )
