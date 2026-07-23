"""Verify Alembic migrations apply and revert cleanly, DB-agnostically, against a
throwaway SQLite database (no Docker/Postgres required for this check)."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(args, db_path):
    env = dict(os.environ)
    env["ENVIRONMENT"] = "test"
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env.pop("REDIS_URL", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND), env=env, capture_output=True, text=True,
    )


def _tables(db_path):
    con = sqlite3.connect(db_path)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_migrations_upgrade_then_downgrade(tmp_path):
    db = tmp_path / "migration_test.db"

    up = _alembic(["upgrade", "head"], db)
    assert up.returncode == 0, up.stderr
    tables = _tables(db)
    assert {"scans", "leads", "rubric_versions"} <= tables

    down = _alembic(["downgrade", "base"], db)
    assert down.returncode == 0, down.stderr
    tables = _tables(db)
    assert "scans" not in tables and "leads" not in tables
