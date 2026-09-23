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
# Force AI OFF by default so the suite NEVER hits the real Anthropic API, even when a
# real ANTHROPIC_API_KEY is present in backend/.env. The AI tests turn it on explicitly
# by monkeypatching settings.anthropic_api_key + app.core.ai.complete.
os.environ["ANTHROPIC_API_KEY"] = ""
# Phase 9: billing gating is OFF by default in tests so pre-billing tests keep full
# access. The billing tests flip settings.billing_enforced on to exercise gating.
os.environ["BILLING_ENFORCED"] = "false"
# AI crawler access check OFF by default so run_scan makes no live per-UA network calls
# during the general suite. The crawler-access tests exercise the service directly with a
# MockTransport, and the scan-integration test enables it + stubs the check explicitly.
os.environ["CRAWLER_ACCESS_ENABLED"] = "false"
# Force email OFF by default so the suite NEVER opens a real SMTP connection or calls the
# real Resend HTTPS API, even when real GMAIL_USER/GMAIL_APP_PASSWORD/RESEND_API_KEY are
# present in backend/.env (email_enabled == resend_configured OR smtp_configured).
# Email-sending tests patch app.services.email_transport.send_email.
os.environ["GMAIL_USER"] = ""
os.environ["GMAIL_APP_PASSWORD"] = ""
os.environ["RESEND_API_KEY"] = ""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _create_schema():
    """Create a clean schema for the test SQLite DB (drop first so re-runs start
    fresh and unique constraints like leads.email don't collide)."""
    from app.db import models  # noqa: F401  (register models)
    from app.db.session import Base, SessionLocal, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # Seed the billing plans (the migration seeds real DBs; create_all does not).
    from app.billing.plans import seed_plans
    db = SessionLocal()
    try:
        seed_plans(db)
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)
