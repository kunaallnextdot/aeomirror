"""Database session + engine.

SQLite is the safe local-dev/test default. Production uses PostgreSQL via
DATABASE_URL (postgresql+psycopg://...), with a small connection pool and
pool_pre_ping so stale connections are detected. Schema creation is NOT done here
at startup anymore — Alembic migrations own the schema (see alembic/ and
INTEGRATIONS.md A2). `init_db()` is kept for local/test convenience only and is
never called during application startup.
"""
from __future__ import annotations

import contextvars

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

# Set (per thread / task context) by the background coordinator. Sessions opened while
# it is set use a SEPARATE, small connection pool, so background jobs can never take
# the connections API requests need. (Several API handlers are `async def` and make
# synchronous queries on the event loop: if the request pool is exhausted, that
# checkout blocks the whole loop until DB_POOL_TIMEOUT — so the request pool must never
# be shared with long-lived background sessions.)
background_context: contextvars.ContextVar[bool] = contextvars.ContextVar("db_background", default=False)


def _make_engine(pool_size: int | None = None, max_overflow: int | None = None):
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
        pool_size=settings.db_pool_size if pool_size is None else pool_size,
        max_overflow=settings.db_max_overflow if max_overflow is None else max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        future=True,
    )


engine = _make_engine()
# SQLite (dev/tests) has no server pool to protect: one engine serves both.
background_engine = engine if settings.uses_sqlite else _make_engine(
    settings.db_background_pool_size, settings.db_background_max_overflow)


_request_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
_background_sessions = sessionmaker(bind=background_engine, autoflush=False, autocommit=False, future=True)


def SessionLocal() -> Session:   # noqa: N802 — kept as the long-standing factory name
    """A new session. Its pool is fixed at creation: the background pool when created in
    the background context (mark_background), the request pool otherwise."""
    return (_background_sessions if background_context.get() else _request_sessions)()


def mark_background() -> None:
    """Route this thread's/task's future sessions to the background pool."""
    background_context.set(True)


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
