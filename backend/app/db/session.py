"""Database session + engine.

SQLite is the safe local-dev/test default. Production uses PostgreSQL via
DATABASE_URL (postgresql+psycopg://...), with a small connection pool and
pool_pre_ping so stale connections are detected. Schema creation is NOT done here
at startup anymore — Alembic migrations own the schema (see alembic/ and
INTEGRATIONS.md A2). `init_db()` is kept for local/test convenience only and is
never called during application startup.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


def _make_engine():
    if settings.uses_sqlite:
        # SQLite: single-file dev/test DB. No server pool semantics.
        return create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    # Server databases (PostgreSQL): bounded pool + liveness check.
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        future=True,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create tables directly from the models.

    LOCAL/TEST CONVENIENCE ONLY. Production must use Alembic migrations
    (`alembic upgrade head`); this is intentionally NOT called on startup.
    """
    from app.db import models  # noqa: F401  (register models on Base)
    Base.metadata.create_all(bind=engine)
