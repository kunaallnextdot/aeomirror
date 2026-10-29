"""Regression test for a real incident: migration b4f7e1a9c2d3 (GA4 Website
Intelligence) was written and merged, but never actually applied to the running
local dev database — every GA4 endpoint touching GoogleAnalyticsConnection,
Ga4TrafficSnapshot, Ga4DailyOverview, or Ga4LandingPageSnapshot returned a 500
("no such column"/"no such table") because the ORM models expected columns the
real database didn't have yet.

The existing test suite uses Base.metadata.create_all() for its DB setup (see
conftest.py), which always produces a schema matching the CURRENT models —
it structurally cannot catch "a migration exists in code but was never run
against some other database." What it CAN catch is the narrower, genuinely
reviewable bug class: the migration's upgrade() not actually producing the same
schema as the models it's supposed to represent. This test applies the real
Alembic migration chain to a fresh, disposable SQLite file (never the app's dev
or test database) and asserts the resulting schema matches what Base.metadata
declares — so a future migration that drifts from its models fails CI, even
though "did someone remember to run alembic upgrade in their own environment"
remains an operational step this suite cannot verify for them."""
import os
import tempfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.db.models import Base

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_alembic_config(sqlite_path: str) -> Config:
    cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{sqlite_path}")
    # alembic/env.py calls logging.config.fileConfig(config.config_file_name) when
    # this is set, and fileConfig's default disable_existing_loggers=True disables
    # every logger that already existed process-wide — breaking caplog-based
    # assertions in unrelated tests later in the SAME pytest session (confirmed:
    # this broke tests/test_snapshot.py when first written without this line).
    # None skips that call entirely; nothing else in this test needs it configured.
    cfg.config_file_name = None
    return cfg


def test_migrating_a_fresh_database_to_head_produces_the_tables_and_columns_the_models_expect(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        sqlite_path = os.path.join(tmp, "migration_check.db")
        # alembic/env.py unconditionally overrides "sqlalchemy.url" with
        # settings.database_url (so migrations always target the same database the
        # app does) — passing a URL only on the Config object, as a first version of
        # this test did, is silently ignored and the migration chain runs against
        # the REAL dev/test database instead. That is exactly as dangerous as it
        # sounds; monkeypatch settings.database_url itself so env.py picks up the
        # scratch file.
        monkeypatch.setattr(settings, "database_url", f"sqlite:///{sqlite_path}")
        command.upgrade(_fresh_alembic_config(sqlite_path), "head")

        engine = create_engine(f"sqlite:///{sqlite_path}")
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names())

        for table_name in ("google_analytics_connections", "ga4_traffic_snapshots",
                           "ga4_daily_overview", "ga4_landing_page_snapshots"):
            assert table_name in actual_tables, (
                f"migration chain never created '{table_name}' — "
                "a model and its migration have drifted apart"
            )
            expected_columns = set(Base.metadata.tables[table_name].columns.keys())
            actual_columns = {c["name"] for c in inspector.get_columns(table_name)}
            missing = expected_columns - actual_columns
            assert not missing, (
                f"'{table_name}' is missing column(s) {missing} after migrating to head — "
                "the migration's upgrade() doesn't match the SQLAlchemy model. This is "
                "exactly the class of bug that caused a real 500 Internal Server Error "
                "on every GA4 endpoint when the migration was merged without matching "
                "the models (or without being applied) — see this file's own docstring."
            )
        engine.dispose()


def test_alembic_head_matches_the_latest_ga4_migration():
    """A simpler tripwire: if someone adds a new migration without updating this
    test, it fails loudly instead of silently passing — forcing a conscious
    decision about whether the new head is still b4f7e1a9c2d3 or something newer
    that should also be covered by the schema-sync test above."""
    cfg = _fresh_alembic_config(os.path.join(tempfile.gettempdir(), "unused.db"))
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == ["b4f7e1a9c2d3"], (
        f"expected alembic head to be b4f7e1a9c2d3, got {heads} — update this test "
        "alongside whatever new migration moved the head"
    )
