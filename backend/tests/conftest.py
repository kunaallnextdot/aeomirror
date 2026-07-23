"""Test config.

Unit tests are isolated from real infra:
- ENVIRONMENT=test, a dedicated SQLite file (not production Postgres),
- REDIS_URL forced empty so cache + rate limiter use the in-memory fallback,
- SSRF DNS resolution bypassed (fetch is mocked) — the dedicated SSRF test and
  the fetch tests re-enable the real check explicitly,
- generous rate limit so the shared client does not trip it.

These env vars are set BEFORE app modules import, so the settings singleton and
the SQLAlchemy engine pick them up. Tables are created here because application
startup no longer calls create_all (Alembic owns the schema in real deployments).
"""
import os

os.environ["ENVIRONMENT"] = "test"
os.environ["ALLOW_PRIVATE_HOSTS"] = "True"
os.environ["FREE_SCANS_PER_WINDOW"] = "1000"
os.environ["DATABASE_URL"] = "sqlite:///./test_aeomirror.db"
os.environ["REDIS_URL"] = ""  # -> in-memory cache + rate limiter

import pytest


@pytest.fixture(autouse=True, scope="session")
def _create_schema():
    """Create a clean schema for the test SQLite DB (drop first so re-runs start
    fresh and unique constraints like leads.email don't collide)."""
    from app.db import models  # noqa: F401  (register models)
    from app.db.session import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
