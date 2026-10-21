"""ORM models for the free-scanner slice. The full product schema is in the PRD."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, Index, Integer, String, Text, false, text,
)

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# Scan lifecycle. Single-page scans are written straight to COMPLETED. Full-site
# scans run as a background job: PENDING -> RUNNING -> COMPLETED | FAILED.
SCAN_PENDING = "pending"
SCAN_RUNNING = "running"
SCAN_COMPLETED = "completed"
SCAN_FAILED = "failed"


class Scan(Base):
    __tablename__ = "scans"
    # Composite indexes for the newest-first dashboard queries. IP-scoped (anonymous,
    # Phase 4) and org-scoped (authenticated, Phase 5).
    __table_args__ = (
        Index("ix_scans_requester_created", "requester_ip_hash", "created_at"),
        Index("ix_scans_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    url = Column(String, nullable=False)
    normalized_url = Column(String, index=True, nullable=False)  # cache key
    ars = Column(Integer, nullable=False)
    rubric_version = Column(String, nullable=False)              # human version string
    # Logical reference to rubric_versions.version this scan was scored with.
    # Nullable so pre-existing rows keep working; backfilled by migration.
    rubric_version_id = Column(String, index=True, nullable=True)
    result = Column(JSON, nullable=False)                        # full report dict
    requester_ip_hash = Column(String, nullable=True)            # hashed, never raw
    lead_email = Column(String, nullable=True)
    # Phase 5: ownership. Nullable so anonymous free scans still work; set when a
    # signed-in user runs a scan, so their org's dashboard is private to them.
    organization_id = Column(String, index=True, nullable=True)
    user_id = Column(String, index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Lifecycle. Single-page scans are created COMPLETED; full-site scans start
    # PENDING and are advanced by the background worker. server_default keeps every
    # pre-existing row valid without a data backfill.
    status = Column(String, nullable=False, default=SCAN_COMPLETED,
                    server_default=SCAN_COMPLETED)
    # Live progress for a running bulk scan (None for single-page scans):
    # {"total": int|null, "done": int, "failed": int, "current_url": str|null}.
    progress = Column(JSON, nullable=True)


class Lead(Base):
    __tablename__ = "leads"
    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False)
    first_scanned_url = Column(String, nullable=True)
    scan_count = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    version = Column(String, primary_key=True)
    family_weights = Column(JSON, nullable=False)
    check_weights = Column(JSON, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    effective_from = Column(DateTime, default=datetime.utcnow)  # created_at semantics


# ============================ Phase 5: auth + accounts ============================
# Role constants (also enforced in the RBAC layer). One role per user/membership.
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER)

# Account status.
STATUS_ACTIVE = "active"
STATUS_INVITED = "invited"
STATUS_SUSPENDED = "suspended"


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)  # stored lowercased
    password_hash = Column(String, nullable=True)   # nullable => OAuth-only (future)
    avatar = Column(String, nullable=True)          # URL or data-URI
    email_verified = Column(Boolean, nullable=False, default=False)
    role = Column(String, nullable=False, default=ROLE_MEMBER)   # effective org role
    status = Column(String, nullable=False, default=STATUS_ACTIVE)
    # Phase 8: platform-level admin (internal admin app). Distinct from the org
    # "admin" role above — a platform admin can see/manage every organization.
    is_platform_admin = Column(Boolean, nullable=False, default=False)
    notification_prefs = Column(JSON, nullable=True)  # {"product_updates": bool, ...}
    # Weekly digest opt-out: a stable per-user token (unsubscribe link) + the flag it sets.
    digest_unsubscribe_token = Column(String, unique=True, index=True, nullable=True)
    digest_opt_out = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    owner_id = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # One-time-ever "bulk trial" (50-URL scan), independent of plan. Flipped True the
    # first time it is consumed; never resets. server_default keeps existing orgs valid.
    used_bulk_trial = Column(Boolean, nullable=False, default=False, server_default="0")


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    # A user belongs to one organization for now, but the membership row is the
    # source of truth for RBAC (role scoped to the org).
    __table_args__ = (
        Index("ix_org_members_org_user", "organization_id", "user_id", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False, default=ROLE_MEMBER)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"
    # A refresh-token session. The raw refresh token is NEVER stored; only its
    # sha256 hash. Rotated on every refresh; revoked on logout / logout-everywhere.
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, index=True, nullable=False)
    refresh_token_hash = Column(String, unique=True, index=True, nullable=False)
    user_agent = Column(String, nullable=True)
    ip_hash = Column(String, nullable=True)         # hashed, never raw
    remember = Column(Boolean, nullable=False, default=False)
    revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, index=True, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, index=True, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invitation(Base):
    __tablename__ = "invitations"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    email = Column(String, index=True, nullable=False)   # stored lowercased
    role = Column(String, nullable=False, default=ROLE_MEMBER)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    invited_by = Column(String, nullable=True)           # user_id of inviter
    status = Column(String, nullable=False, default="pending")  # pending|accepted|revoked
    accepted_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================ Phase 6: reports ============================
class Report(Base):
    __tablename__ = "reports"
    # Latest generated report for a scan (regenerated on demand). Stores the full
    # rule-based report JSON so exports don't recompute, plus version + timestamp.
    id = Column(String, primary_key=True, default=_uuid)
    scan_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=True)
    version = Column(String, nullable=False)            # report engine version
    overall_score = Column(Integer, nullable=True)
    recommendation_count = Column(Integer, nullable=True)
    data = Column(JSON, nullable=False)                 # full report dict
    generated_at = Column(DateTime, default=datetime.utcnow)


class ReportExport(Base):
    __tablename__ = "report_exports"
    # One row per download — the export history.
    id = Column(String, primary_key=True, default=_uuid)
    report_id = Column(String, index=True, nullable=True)
    scan_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=True)
    user_id = Column(String, index=True, nullable=True)
    format = Column(String, nullable=False)             # pdf | csv | json
    size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportShare(Base):
    """A public, read-only share link for one scan's report. The token is stored so an
    org member can RETRIEVE their own active share URL (a share token is not a
    credential — it only grants read to a report the org already owns and can view; see
    the TTL/entropy notes). A share is valid iff token matches AND revoked_at IS NULL
    AND expires_at > now. Viewer IPs are NOT stored (count-only), by design."""
    __tablename__ = "report_shares"
    id = Column(String, primary_key=True, default=_uuid)
    token = Column(String, unique=True, index=True, nullable=False)   # 384-bit opaque; retrievable by owner
    scan_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)   # scopes list/revoke to the owner
    created_by = Column(String, nullable=True)                     # user_id
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    last_viewed_at = Column(DateTime, nullable=True)


# ==================== Change Attribution (scan snapshots) ====================
class ScanSnapshot(Base):
    """A normalised, deterministic snapshot of a scanned page, captured so a later scan's
    score movement can be EXPLAINED (the diff engine compares consecutive snapshots for
    the same monitor). `schema_version` gates the diff — the engine refuses to compare
    across incompatible payload shapes rather than emit garbage. Columns follow the
    codebase convention (plain indexed String references, portable JSON payload)."""
    __tablename__ = "scan_snapshots"
    __table_args__ = (
        # Supports "the previous snapshot for this monitor" (order by created_at desc).
        Index("ix_scan_snapshots_monitor_created", "monitor_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    scan_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)     # null for non-monitor scans
    organization_id = Column(String, index=True, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    payload = Column(JSON, nullable=False)                     # deterministic snapshot dict
    created_at = Column(DateTime, default=datetime.utcnow)


# ==================== AI Content Insights (Pro-only, per page) ====================
class AiContentInsight(Base):
    """Cached AI content-quality analysis for one page of a scan (Feature B). Generated
    on demand by Pro users; the (scan_id, page_url) pair is unique so repeat requests
    return the stored row instead of re-calling the model."""
    __tablename__ = "ai_content_insights"
    __table_args__ = (
        Index("ix_ai_insights_scan_page", "scan_id", "page_url", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    scan_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=True)
    page_url = Column(String, nullable=False)
    data = Column(JSON, nullable=False)          # {tone, clarity, structure, suggestions, rewrite_example}
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================ Phase 7: monitoring ============================
FREQUENCIES = ("daily", "weekly", "monthly", "manual")
MONITOR_ACTIVE = "active"
MONITOR_PAUSED = "paused"

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

# Job kinds. Monitor scans reuse "scheduled"/"manual"; a full-site scan is "site_scan".
JOB_KIND_SCHEDULED = "scheduled"
JOB_KIND_MANUAL = "manual"
JOB_KIND_BULK_SCAN = "bulk_scan"
JOB_KIND_GA4_SYNC = "ga4_sync"
JOB_KIND_URL_CRAWL = "url_crawl"   # one bounded chunk of a CrawlJob; re-enqueues itself

ALERT_OPEN = "open"
ALERT_ACK = "acknowledged"


class Monitor(Base):
    __tablename__ = "monitors"
    __table_args__ = (
        Index("ix_monitors_org_created", "organization_id", "created_at"),
        Index("ix_monitors_due", "status", "next_scan_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=True)      # creator
    name = Column(String, nullable=True)
    url = Column(String, nullable=False)
    normalized_url = Column(String, index=True, nullable=False)
    frequency = Column(String, nullable=False, default="weekly")   # daily|weekly|monthly|manual
    status = Column(String, nullable=False, default=MONITOR_ACTIVE)  # active|paused
    created_at = Column(DateTime, default=datetime.utcnow)
    last_scan_at = Column(DateTime, nullable=True)
    next_scan_at = Column(DateTime, nullable=True)
    latest_scan_id = Column(String, nullable=True)
    latest_score = Column(Integer, nullable=True)
    digest_enabled = Column(Boolean, nullable=False, default=True)   # include in the weekly digest
    # Answer Tracking brand identity — describes THIS site, so it lives on the monitor (a
    # site has exactly one prompt list). Seeded from name/url; independently editable
    # (legal entity name often differs from the marketed brand name). Drives LLM extraction.
    brand_name = Column(String, nullable=True)
    brand_domain = Column(String, nullable=True)
    brand_aliases = Column(JSON, nullable=True)              # list[str]
    competitor_domains = Column(JSON, nullable=True)         # list[str], max 5
    # First-party website analytics (AI Business Impact): a sha256 hash of the secret
    # the SITE'S OWN backend presents (Bearer) to POST /monitors/{id}/tracking/confirm
    # to mark a purchase/booking as verified — the raw key is shown to the org ONCE
    # (on generation) and never stored or logged in plaintext, same convention as
    # Session.refresh_token_hash. Nullable until first generated.
    tracking_api_key_hash = Column(String, nullable=True)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    # The job queue. Claim-based so a future distributed worker can process it:
    # a worker atomically flips pending -> running, then completed/failed. At most
    # one active (pending/running) job per monitor is enforced in the scheduler.
    __table_args__ = (
        Index("ix_jobs_claimable", "status", "run_after"),
        Index("ix_jobs_monitor_status", "monitor_id", "status"),
        Index("ix_jobs_connection_status", "connection_id", "status"),
        # At most ONE active (pending|running) job per monitor — enforced atomically at the
        # DB level so two concurrent scheduler processes cannot both enqueue the same
        # monitor (partial unique index; NULL monitor_id bulk jobs are excluded). Same
        # predicate on SQLite + Postgres so create_all (tests) and Alembic (prod) match.
        Index("uq_scheduled_jobs_active_per_monitor", "monitor_id", unique=True,
              sqlite_where=text("status IN ('pending','running') AND monitor_id IS NOT NULL"),
              postgresql_where=text("status IN ('pending','running') AND monitor_id IS NOT NULL")),
        # Same pattern for ga4_sync jobs: at most one active sync per GA4 connection.
        Index("uq_scheduled_jobs_active_per_connection", "connection_id", unique=True,
              sqlite_where=text("status IN ('pending','running') AND connection_id IS NOT NULL"),
              postgresql_where=text("status IN ('pending','running') AND connection_id IS NOT NULL")),
        # Same pattern for url_crawl jobs: at most one active chunk per crawl job, so
        # two ticks can never process the same CrawlJob concurrently.
        Index("uq_scheduled_jobs_active_per_crawl_job", "crawl_job_id", unique=True,
              sqlite_where=text("status IN ('pending','running') AND crawl_job_id IS NOT NULL"),
              postgresql_where=text("status IN ('pending','running') AND crawl_job_id IS NOT NULL")),
    )
    id = Column(String, primary_key=True, default=_uuid)
    # Monitor jobs reference a monitor; site_scan jobs reference a scan instead;
    # ga4_sync jobs reference a GoogleAnalyticsConnection; url_crawl jobs reference a
    # CrawlJob — all nullable, and the worker dispatches on `kind`.
    monitor_id = Column(String, index=True, nullable=True)
    scan_id = Column(String, index=True, nullable=True)          # set for kind="site_scan"
    connection_id = Column(String, index=True, nullable=True)    # set for kind="ga4_sync"
    crawl_job_id = Column(String, index=True, nullable=True)     # set for kind="url_crawl"
    organization_id = Column(String, index=True, nullable=True)
    kind = Column(String, nullable=False, default="scheduled")   # scheduled|manual|site_scan|ga4_sync
    status = Column(String, nullable=False, default=JOB_PENDING)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    scheduled_for = Column(DateTime, nullable=True)   # when it became due
    run_after = Column(DateTime, nullable=True)       # earliest claim time (backoff)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class MonitorHistory(Base):
    __tablename__ = "monitor_history"
    # One row per completed scan of a monitor. Stores a compact snapshot (scores +
    # issue count + detected changes) so trend/history retrieval never re-parses scans.
    __table_args__ = (
        Index("ix_history_monitor_created", "monitor_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    monitor_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=True)
    scan_id = Column(String, nullable=False)
    overall_score = Column(Integer, nullable=True)
    status = Column(String, nullable=True)              # pass|warn|fail
    issue_count = Column(Integer, nullable=True)
    scores = Column(JSON, nullable=True)                # {signal_id: score}
    changes = Column(JSON, nullable=True)               # diff vs previous scan
    created_at = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_org_created", "organization_id", "created_at"),
        Index("ix_alerts_monitor_created", "monitor_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    monitor_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=True)
    scan_id = Column(String, nullable=True)
    type = Column(String, nullable=False)               # score_drop|robots_blocked|...
    severity = Column(String, nullable=False)           # critical|warning|info
    title = Column(String, nullable=False)
    message = Column(String, nullable=True)
    detail = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default=ALERT_OPEN)  # open|acknowledged
    acknowledged_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notiflog_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=True)
    user_id = Column(String, index=True, nullable=True)
    monitor_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)     # critical_alert|weekly_summary|monthly_summary
    channel = Column(String, nullable=False, default="email")
    subject = Column(String, nullable=True)
    status = Column(String, nullable=False)   # sent|skipped|failed
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================ Phase 8: admin platform ============================
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_action_created", "action", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    actor_id = Column(String, index=True, nullable=True)
    actor_email = Column(String, nullable=True)
    action = Column(String, nullable=False)          # admin_login|delete_user|...
    target_type = Column(String, nullable=True)      # user|organization|scan|monitor|settings
    target_id = Column(String, index=True, nullable=True)
    meta = Column(JSON, nullable=True)
    ip_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    name = Column(String, primary_key=True)          # monitoring|pdf_export|email|alerts|experimental
    enabled = Column(Boolean, nullable=False, default=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)           # scanner_version|default_rubric|maintenance_mode|...
    value = Column(JSON, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ============================ Phase 9: billing ============================
PLAN_FREE = "free"
PLAN_REPORT = "report"
PLAN_PRO = "pro"

SUB_ACTIVE = "active"
SUB_CANCELED = "canceled"
SUB_PAST_DUE = "past_due"
SUB_INCOMPLETE = "incomplete"

PAY_PENDING = "pending"
PAY_SUCCEEDED = "succeeded"
PAY_FAILED = "failed"
PAY_REFUNDED = "refunded"
PAY_DISPUTED = "disputed"

KIND_SUBSCRIPTION = "subscription"
KIND_ONE_TIME_REPORT = "one_time_report"


class Plan(Base):
    __tablename__ = "plans"
    code = Column(String, primary_key=True)          # free | report | pro
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")
    interval = Column(String, nullable=True)         # None | one_time | month
    features = Column(JSON, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subs_org_status", "organization_id", "status"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    plan_code = Column(String, nullable=False)
    status = Column(String, nullable=False, default=SUB_ACTIVE)
    provider = Column(String, nullable=False, default="stripe")
    provider_subscription_id = Column(String, index=True, nullable=True)
    provider_customer_id = Column(String, nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)
    canceled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=True)
    provider = Column(String, nullable=False, default="stripe")
    provider_payment_id = Column(String, index=True, nullable=True)
    kind = Column(String, nullable=False)            # subscription | one_time_report
    plan_code = Column(String, nullable=True)
    scan_id = Column(String, index=True, nullable=True)   # set for one_time_report
    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")
    status = Column(String, nullable=False, default=PAY_PENDING)
    reference = Column(String, index=True, nullable=True)  # our checkout reference
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    # Raw provider webhook events — the idempotency + audit ledger. A duplicate
    # provider_event_id is ignored so a re-delivered webhook can't double-charge.
    id = Column(String, primary_key=True, default=_uuid)
    provider = Column(String, nullable=False, default="stripe")
    provider_event_id = Column(String, unique=True, index=True, nullable=True)
    type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    processed = Column(Boolean, nullable=False, default=False)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    subscription_id = Column(String, index=True, nullable=True)
    payment_id = Column(String, index=True, nullable=True)
    number = Column(String, unique=True, nullable=False)
    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")
    status = Column(String, nullable=False, default="paid")   # paid | open | void
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    description = Column(String, nullable=True)
    issued_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================ Usage metering (billing) ============================
# Kinds of metered usage counted against per-plan monthly quotas.
USAGE_COMPARE = "compare"
# One user-initiated scan job (single-page today; a bulk-50 scan counts as one too).
# Recorded ONLY on the API scan path so monitor-triggered scans never consume quota.
USAGE_SCAN_JOB = "scan_job"
# One successful AI report-narrative generation (any tier). Powers the per-org monthly
# free cap and the global daily ceiling; recorded once per real Anthropic call.
USAGE_AI_NARRATIVE = "ai_narrative"


class UsageEvent(Base):
    """A single metered action by an org (e.g. a scan comparison). Rows are cheap
    and append-only; monthly quotas are computed by counting rows since the start of
    the calendar month. Kept separate from domain tables (scans/monitors) so new
    metered actions can be added without touching them."""
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_org_kind_created", "organization_id", "kind", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    kind = Column(String, nullable=False)            # e.g. "compare"
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ============================ Contact & Support ============================
# Lifecycle of a support request in the admin Support Inbox.
CONTACT_NEW = "new"        # just submitted, unread
CONTACT_OPEN = "open"      # acknowledged / being worked on
CONTACT_CLOSED = "closed"  # resolved
CONTACT_STATUSES = (CONTACT_NEW, CONTACT_OPEN, CONTACT_CLOSED)


class Contact(Base):
    """A contact-form / support submission. Stored FIRST and always; the
    notification + confirmation emails are strictly best-effort (see
    app.services.contact_service). All fields are sanitized on the way in and
    HTML-escaped wherever they are rendered (emails) — the API returns JSON only."""
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_status_created", "status", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    website = Column(String, nullable=True)              # optional
    subject = Column(String, nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False, default=CONTACT_NEW, index=True)
    # Hashed requester IP (never the raw IP) — for spam triage / rate-limit audit.
    ip_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# =====================================================================
# AI Answer Tracking (Part A) — data model only. Measures whether AI
# assistants mention/cite a brand. This layer executes prompts and stores
# raw responses; it performs NO analysis (mention detection, sentiment,
# Share of Voice, etc. are Part B). No FK constraints (codebase convention):
# links are plain indexed String columns. Every table carries organization_id
# so admin_delete_org can purge it.
# =====================================================================
# prompt_runs.status lifecycle:
RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"     # every call succeeded
RUN_FAILED = "failed"           # every call failed
RUN_PARTIAL = "partial"         # some calls failed; successful results ARE persisted
PROMPT_RUN_STATUSES = (RUN_PENDING, RUN_RUNNING, RUN_COMPLETED, RUN_FAILED, RUN_PARTIAL)
_RUN_TERMINAL = (RUN_COMPLETED, RUN_FAILED, RUN_PARTIAL)   # answer phase finished

# prompt_runs.extraction_status lifecycle (Part B — the analysis phase). Distinct from
# `status` (the answer phase) so the UI can show "Running" -> "Analysing" -> "Complete".
EXTRACTION_PENDING = "pending"
EXTRACTION_RUNNING = "running"
EXTRACTION_COMPLETE = "complete"
EXTRACTION_FAILED = "failed"


class PromptSet(Base):
    """A named collection of prompts tracked for one org (optionally tied to a
    monitor, which seeds the brand identity). Brand fields (Part B) drive the LLM
    extraction; they are seeded from the linked monitor but independently editable
    (the legal entity name and the marketed brand name often differ)."""
    __tablename__ = "prompt_sets"
    __table_args__ = (Index("ix_prompt_sets_org", "organization_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)   # optional link to a Monitor
    name = Column(String, nullable=False)
    # Part B — brand identity for extraction (mention detection is the LLM's job, not
    # substring matching; aliases are supplied to the model as context).
    brand_name = Column(String, nullable=True)
    brand_domain = Column(String, nullable=True)
    brand_aliases = Column(JSON, nullable=True)              # list[str]
    competitor_domains = Column(JSON, nullable=True)         # list[str], max 5
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrackedPrompt(Base):
    """One prompt string within a set. Executed multiple times per run (see run_index)."""
    __tablename__ = "tracked_prompts"
    __table_args__ = (Index("ix_tracked_prompts_set", "prompt_set_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    prompt_set_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)
    text = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    # Provenance only — never changes execution semantics. "question_bank" means this
    # prompt was created FROM a Question Bank entry (scan FAQ/heading text or an
    # already-tracked prompt) rather than typed by hand; see services/answer_simulator.
    source = Column(String, nullable=False, default="manual", server_default="manual")
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptRun(Base):
    """One execution of a prompt set: every active prompt x enabled provider x
    run_index. Aggregates call counts and an estimated cost; per-call detail lives
    in prompt_results."""
    __tablename__ = "prompt_runs"
    __table_args__ = (Index("ix_prompt_runs_set_created", "prompt_set_id", "created_at"),)
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    prompt_set_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)   # the site this run belongs to
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default=RUN_PENDING)
    total_calls = Column(Integer, nullable=False, default=0)
    failed_calls = Column(Integer, nullable=False, default=0)
    estimated_cost_usd = Column(Float, nullable=False, default=0.0)
    # Part B — the analysis phase, tracked separately from the answer phase above.
    extraction_status = Column(String, nullable=False, default=EXTRACTION_PENDING)
    # "provider_tracking" (existing OpenAI/Anthropic/Perplexity/Gemini flow, unchanged)
    # or "simulator" (the deterministic/optional-local-LLM AEO Answer Simulator).
    run_mode = Column(String, nullable=False, default="provider_tracking",
                      server_default="provider_tracking")
    # Simulator runs only: whether the optional LLM step was requested for any
    # question in this run (a deterministic-only batch leaves this False).
    llm_step_requested = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptResult(Base):
    """A single provider call. A FAILED call is data, not a gap — it is persisted
    with its `error` set. `citations = None` means the provider CANNOT report
    citations; an empty list means it searched and cited nothing (never conflate)."""
    __tablename__ = "prompt_results"
    __table_args__ = (Index("ix_prompt_results_run_prompt", "run_id", "prompt_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, index=True, nullable=False)
    prompt_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)   # the site this result belongs to
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)          # exact model string (never a floating alias)
    run_index = Column(Integer, nullable=False)     # 0..runs_per_prompt-1 (base); == runs_per_prompt for adaptive
    is_adaptive_run = Column(Boolean, nullable=False, default=False)
    search_enabled = Column(Boolean, nullable=False, default=True)
    raw_response = Column(Text, nullable=True)
    citations = Column(JSON, nullable=True)         # None = can't report; [] = searched, cited nothing
    latency_ms = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    # Simulator rows only (null for provider_tracking rows). See
    # services/answer_simulator/scoring.py for the HIGH|MEDIUM|LOW|INSUFFICIENT_EVIDENCE
    # enum and the evidence_coverage_pct calculation.
    answerability = Column(String, nullable=True)
    evidence_coverage_pct = Column(Float, nullable=True)
    # Off-topic/low-relevance guard (see services/answer_simulator/topic_alignment.py)
    # — "does this question align with the site's evidence at all", a separate
    # signal from evidence_coverage_pct (how strongly retrieval matched).
    topic_alignment_score = Column(Float, nullable=True)
    question_token_coverage = Column(Float, nullable=True)
    llm_step_used = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptResultAnalysis(Base):
    """Part B — the structured extraction for ONE prompt_results row. Kept separate from
    prompt_results so extraction can be re-run against stored raw responses without
    re-calling the expensive answer providers.

    `extraction_failed=true` means the LLM output could not be parsed — it is NOT a
    negative result. A failed extraction is EXCLUDED from mention-rate denominators;
    never conflate it with brand_mentioned=false. `raw_output` keeps the unparseable
    text for debugging/re-analysis."""
    __tablename__ = "prompt_result_analysis"
    __table_args__ = (Index("ix_prompt_result_analysis_run", "run_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    result_id = Column(String, index=True, nullable=False)
    run_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)   # the site this analysis belongs to
    brand_mentioned = Column(Boolean, nullable=True)          # null when extraction_failed
    mention_context = Column(Text, nullable=True)             # the sentence containing the mention
    sentiment = Column(String, nullable=True)                 # positive | neutral | negative | null
    brand_urls_cited = Column(JSON, nullable=True)            # list[str]
    # competitors_mentioned: list[{name, domain_if_stated, mention_type}] — mention_type is
    # "recommendation"|"comparison"|"example"|"news"|"other". Only solution types count as
    # competitors (an entity named as an example/case study/news subject is NOT a competitor).
    competitors_mentioned = Column(JSON, nullable=True)
    # recommended_entities: ORDERED list[{name, domain_if_stated}] the model surfaced as
    # SOLUTIONS for this query — the "who was recommended instead" when the brand is absent.
    recommended_entities = Column(JSON, nullable=True)
    position = Column(Integer, nullable=True)                 # 1-based rank in a list answer, else null
    extraction_failed = Column(Boolean, nullable=False, default=False)
    extraction_model = Column(String, nullable=False)         # exact model string used to extract
    raw_output = Column(Text, nullable=True)                  # unparseable LLM output (on failure)
    # Simulator rows only (null for provider_tracking rows).
    missing_information = Column(JSON, nullable=True)         # list[str]
    simulator_confidence = Column(String, nullable=True)      # "high"|"medium"|"low"|null
    created_at = Column(DateTime, default=datetime.utcnow)


class PromptGapAnalysis(Base):
    """Part B — gap-to-action for a zero-mention prompt in a run. ONE cheap LLM call per
    prompt per run (never per sample), grounded in the site's own scan findings. Cached
    here so it is not regenerated except on reanalyse."""
    __tablename__ = "prompt_gap_analysis"
    __table_args__ = (Index("ix_prompt_gap_analysis_run", "run_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, index=True, nullable=False)
    prompt_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)   # the site this gap analysis belongs to
    why = Column(Text, nullable=True)                # 2 sentences max, or a plain "not enough signal"
    actions = Column(JSON, nullable=True)            # list[str], 2-4 concrete actions (may be empty)
    has_signal = Column(Boolean, nullable=False, default=True)   # False => said "not enough signal"
    model = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SimulatorEvidence(Base):
    """One retrieved evidence unit that backed an AEO Answer Simulator answer (see
    services/answer_simulator/). Kept separate from PromptResult.citations (the
    provider-tracking citations contract: [{url,title}] | [] | None) since simulator
    evidence also carries the FIELD it came from, its retrieval score, and matched
    terms — a retrieval audit trail, not a citation list. One row per unit surfaced to
    a given answer (top_k per question, small)."""
    __tablename__ = "simulator_evidence"
    __table_args__ = (Index("ix_simulator_evidence_result", "result_id"),)
    id = Column(String, primary_key=True, default=_uuid)
    result_id = Column(String, index=True, nullable=False)   # -> prompt_results.id
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=True)
    url = Column(String, nullable=False)
    field = Column(String, nullable=False)          # "title"|"h1"|"faq_question"|"heading_question"|...
    snippet = Column(Text, nullable=False)           # verbatim evidence text (already capped upstream)
    score = Column(Float, nullable=False)            # boosted retrieval score
    matched_terms = Column(JSON, nullable=True)       # list[str]
    rank = Column(Integer, nullable=False)            # 1-based order surfaced to the answer/LLM
    created_at = Column(DateTime, default=datetime.utcnow)


class Verification(Base):
    """A persisted Fix Verification result (see services/verification.py — the ONE
    comparison engine, a 1:1 port of frontend/src/dashboard/verification.js, never a
    second/divergent algorithm). References the two existing Scan rows it was
    computed from rather than duplicating either scan's full result payload — the
    derived comparison (status/score deltas + issue/evidence diffs) is the only new
    data this table stores. One row per "Verify" action; multiple rows can exist for
    the same (baseline_scan_id, signal_id) pair over time (see PHASE 8 history)."""
    __tablename__ = "verifications"
    __table_args__ = (
        Index("ix_verifications_baseline_signal", "baseline_scan_id", "signal_id", "created_at"),
        Index("ix_verifications_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)   # derived from ctx, never client input
    baseline_scan_id = Column(String, index=True, nullable=False)  # -> scans.id
    verification_scan_id = Column(String, index=True, nullable=False)   # -> scans.id
    signal_id = Column(String, nullable=False)        # stable scanner signal id (same as recommendations)
    verification_status = Column(String, nullable=False)   # verified|partially_improved|unchanged|regressed
    status_before = Column(String, nullable=True)
    status_after = Column(String, nullable=True)
    score_before = Column(Float, nullable=True)
    score_after = Column(Float, nullable=True)
    resolved_issues = Column(JSON, nullable=False, default=list)
    remaining_issues = Column(JSON, nullable=False, default=list)
    new_issues = Column(JSON, nullable=False, default=list)
    evidence_changes = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# --- Google Analytics 4 integration (P0: AI referral traffic) ---
# See app/services/integrations/google_analytics.py (OAuth + sync) and
# app/services/integrations/classification.py (AI-source labeling).
GA4_STATUS_PENDING_PROPERTY = "pending_property"   # OAuth done, no property chosen yet
GA4_STATUS_ACTIVE = "active"
GA4_STATUS_REVOKED = "revoked"
GA4_STATUS_ERROR = "error"

# Deterministic classification buckets (see classification.py) — "unattributable"
# specifically covers Google organic search, which the GA4 API cannot separate from
# an AI Overview click. Never a database CHECK constraint: the label vocabulary is
# allowed to grow (e.g. a new assistant) without a migration.
AI_SOURCE_LABELS = (
    "chatgpt", "perplexity", "gemini", "copilot", "claude",
    "other_identifiable", "unattributable",
)


class OAuthState(Base):
    """A short-lived, single-use anti-CSRF/anti-replay token for the Google OAuth
    authorization-code flow. /connect creates one bound to the authenticated caller's
    org+user and embeds only its opaque `state` string in the redirect to Google;
    /callback (which Google redirects the BROWSER to directly — it can carry no
    Authorization header) looks up this row by `state` to recover who initiated the
    flow, rather than trusting anything the browser or Google's redirect supplies.
    Expired or already-consumed rows are rejected (see services/integrations/
    google_analytics.py::consume_oauth_state)."""
    __tablename__ = "oauth_states"
    __table_args__ = (
        Index("ix_oauth_states_expires", "expires_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    provider = Column(String, nullable=False, default="google")
    state = Column(String, unique=True, index=True, nullable=False)
    # PKCE code_verifier (RFC 7636) — generated and held server-side only; never sent
    # to the browser. Defense in depth against authorization-code interception.
    code_verifier = Column(String, nullable=False)
    consumed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class GoogleAnalyticsConnection(Base):
    """One organization's connected Google account + selected GA4 property. At most
    one row per organization (reused across connect/disconnect/reconnect cycles, so
    sync history in Ga4TrafficSnapshot stays attached to a stable connection_id — see
    the unique index below). `encrypted_refresh_token` is the ONLY credential
    persisted (see app/core/token_crypto.py); the short-lived access token is never
    stored, only held in memory for the duration of a single sync/API call. A
    connection with `ga4_property_id IS NULL` is mid-setup and must never be synced
    (enforced in the scheduler's enqueue_due_ga4_syncs, not just the API layer).
    No FK constraints (codebase convention — see ScheduledJob/Monitor): organization_id
    and connection_id elsewhere are plain indexed String columns."""
    __tablename__ = "google_analytics_connections"
    __table_args__ = (
        Index("ix_ga4_conn_org", "organization_id", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    google_account_email = Column(String, nullable=True)   # informational display only
    encrypted_refresh_token = Column(Text, nullable=True)  # None while status=revoked
    ga4_property_id = Column(String, nullable=True)
    ga4_property_name = Column(String, nullable=True)
    connected_by_user_id = Column(String, nullable=False)
    connected_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String, nullable=True)       # success|error|never
    # Safe, non-sensitive code only — never a raw exception message or API error body.
    last_sync_error_code = Column(String, nullable=True)
    next_sync_at = Column(DateTime, nullable=True, index=True)
    status = Column(String, nullable=False, default=GA4_STATUS_PENDING_PROPERTY)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Ga4TrafficSnapshot(Base):
    """One aggregated daily row from a GA4 runReport call: a (date, channel grouping,
    source, medium) combination's session count, plus the deterministic AI-source
    classification of that combination (see classification.py — computed at sync
    time and stored, not recomputed per-request, so a future classification-rule
    change doesn't silently rewrite historical dashboards). Aggregated counts only —
    never raw per-visitor events (P0 scope). `date` is the GA4 API's own "YYYYMMDD"
    dimension value, stored verbatim as a string (avoids timezone-conversion
    ambiguity and sorts correctly lexicographically for range queries)."""
    __tablename__ = "ga4_traffic_snapshots"
    __table_args__ = (
        Index("ix_ga4_snap_org_date", "organization_id", "date"),
        Index("uq_ga4_snap_dimension", "connection_id", "date", "channel_grouping",
              "source", "medium", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    connection_id = Column(String, index=True, nullable=False)
    date = Column(String, nullable=False)                  # "YYYYMMDD", GA4's own format
    channel_grouping = Column(String, nullable=False, default="(not set)")
    source = Column(String, nullable=False, default="(not set)")
    medium = Column(String, nullable=False, default="(not set)")
    ai_source_label = Column(String, nullable=False)       # one of AI_SOURCE_LABELS
    sessions = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# --- First-party website analytics (AI Business Impact) ---
# GA4-independent: a lightweight tracker.js installed on a customer's site posts
# pageview/conversion events to POST /t/collect, keyed by a Monitor's id as the
# public "site id" (reuses the existing Monitor = "a site this org tracks" entity
# rather than inventing a parallel site model). See
# app/services/analytics/classification.py (channel rules) and
# app/api/routes_tracking.py (the public collector + tracker.js).
ANALYTICS_CHANNELS = (
    "chatgpt", "perplexity", "gemini", "copilot", "claude", "other_identifiable_ai",
    "organic_search", "direct", "paid", "social", "email", "referral", "unknown",
)
CONVERSION_TYPES = (
    "purchase", "lead_submitted", "booking_confirmed", "signup_completed",
    "contact_submitted", "custom",
)


class AnalyticsSession(Base):
    """One browser session on one tracked site. `channel`/`referrer_host`/`utm_*` are
    the FIRST-TOUCH attribution for this session and are set ONLY at creation — never
    overwritten by a later same-session pageview with internal or missing referrer
    (see services/analytics/ingest.py). `visitor_id` is a long-lived, first-party id
    (tracker-generated, stored in the visitor's own localStorage) used only to tell
    new vs. returning sessions apart — never a cross-site identifier."""
    __tablename__ = "analytics_sessions"
    __table_args__ = (
        Index("uq_analytics_sessions_monitor_session", "monitor_id", "session_id", unique=True),
        Index("ix_analytics_sessions_org_started", "organization_id", "started_at"),
        Index("ix_analytics_sessions_monitor_started", "monitor_id", "started_at"),
        Index("ix_analytics_sessions_visitor", "monitor_id", "visitor_id"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=False)
    session_id = Column(String, nullable=False)          # client-generated (sessionStorage)
    visitor_id = Column(String, nullable=True)            # client-generated (localStorage)
    is_returning = Column(Boolean, nullable=False, default=False)
    landing_url = Column(String, nullable=False)
    referrer_host = Column(String, nullable=True)
    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)
    channel = Column(String, nullable=False)              # one of ANALYTICS_CHANNELS
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    page_view_count = Column(Integer, nullable=False, default=0)


class AnalyticsPageview(Base):
    """One pageview within a session — kept separate from AnalyticsSession (rather than
    just incrementing a counter) so landing-page and per-URL performance can be
    aggregated without re-deriving it from a session's single landing_url."""
    __tablename__ = "analytics_pageviews"
    __table_args__ = (
        Index("ix_analytics_pv_monitor_created", "monitor_id", "created_at"),
        Index("ix_analytics_pv_session", "analytics_session_id"),
        Index("uq_analytics_pv_event", "monitor_id", "event_id", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=False)
    analytics_session_id = Column(String, nullable=False)   # -> analytics_sessions.id
    event_id = Column(String, nullable=False)   # client-generated idempotency key
    url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AnalyticsConversion(Base):
    """A purchase/lead/booking/signup/custom conversion. `verified=False` means it was
    only ever client-reported (the public collector accepts these, but they are never
    treated as proof of a real transaction); `verified=True` means it was created or
    confirmed via the authenticated, per-site server-to-server confirm endpoint — the
    ONLY path that counts toward "confirmed" revenue/booking metrics. Deduplicated per
    (monitor, event_type, external_id): a client-reported row and a later server
    confirmation for the SAME external_id update the same row rather than double
    counting."""
    __tablename__ = "analytics_conversions"
    __table_args__ = (
        Index("ix_analytics_conv_monitor_created", "monitor_id", "created_at"),
        Index("uq_analytics_conv_external", "monitor_id", "event_type", "external_id", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    monitor_id = Column(String, index=True, nullable=False)
    analytics_session_id = Column(String, nullable=True)   # -> analytics_sessions.id, if correlated
    event_type = Column(String, nullable=False)             # one of CONVERSION_TYPES
    event_name = Column(String, nullable=True)              # label when event_type="custom"
    external_id = Column(String, nullable=False)            # stable dedupe key (order/booking/lead id)
    value_cents = Column(Integer, nullable=True)
    currency = Column(String, nullable=True)                # ISO 4217, e.g. "USD"
    verified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== URL Intelligence Engine + AI Expert Analysis ====================
# A CrawlJob discovers/crawls/analyzes up to `max_urls` pages of one monitored site,
# in bounded chunks processed by the SAME ScheduledJob queue the monitor/bulk-scan/ga4
# jobs already use (kind=JOB_KIND_URL_CRAWL) — never a second queue or worker. Each
# chunk advances `phase`/`cursor` and re-enqueues a continuation job if work remains,
# which is what makes a 100k-URL crawl resumable instead of one long-running task.
CRAWL_PENDING = "pending"
CRAWL_DISCOVERING = "discovering"
CRAWL_CRAWLING = "crawling"
CRAWL_ANALYZING = "analyzing"
CRAWL_COMPLETED = "completed"
CRAWL_FAILED = "failed"
CRAWL_CANCELLED = "cancelled"
CRAWL_PHASES = (CRAWL_PENDING, CRAWL_DISCOVERING, CRAWL_CRAWLING, CRAWL_ANALYZING,
                CRAWL_COMPLETED, CRAWL_FAILED, CRAWL_CANCELLED)

URL_DISCOVERED = "discovered"    # found (sitemap/internal link), not yet eligibility-checked
URL_ELIGIBLE = "eligible"        # SSRF+robots+dedup passed, queued to crawl
URL_CRAWLED = "crawled"          # fetched + deterministic evidence extracted
URL_SKIPPED = "skipped"          # ineligible (robots/ssrf/duplicate/over_limit/non_html)
URL_FAILED = "failed"            # fetch/parse error

FINDING_OPEN = "open"
FINDING_IN_PROGRESS = "in_progress"
FINDING_FIXED = "fixed"
FINDING_WONT_FIX = "wont_fix"
FINDING_STATUSES = (FINDING_OPEN, FINDING_IN_PROGRESS, FINDING_FIXED, FINDING_WONT_FIX)

# Mirrors app.services.verification's state vocabulary so the two "has this improved"
# concepts stay consistent across the product, even though the comparison here is
# evidence-based (per-URL) rather than signal-score-based (per-scan).
VERIFY_NOT_COMPARABLE = "not_comparable"
VERIFY_VERIFIED = "verified"
VERIFY_PARTIALLY_IMPROVED = "partially_improved"
VERIFY_UNCHANGED = "unchanged"
VERIFY_REGRESSED = "regressed"

USAGE_URL_AI_ANALYSIS = "url_ai_analysis"   # UsageEvent kind: one row per AI template call


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        Index("ix_crawl_jobs_org_created", "organization_id", "created_at"),
        Index("ix_crawl_jobs_monitor", "monitor_id"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=True)
    monitor_id = Column(String, index=True, nullable=False)
    seed_url = Column(String, nullable=False)
    normalized_seed_url = Column(String, nullable=False)
    max_urls = Column(Integer, nullable=False)
    phase = Column(String, nullable=False, default=CRAWL_PENDING)
    # Resumability checkpoint for the current phase: discovery frontier / remaining
    # sitemap URLs / crawl cursor / analyze cursor. Shape is private to
    # app.services.url_intelligence.discovery — never read elsewhere.
    cursor = Column(JSON, nullable=True)
    discovered_count = Column(Integer, nullable=False, default=0)
    eligible_count = Column(Integer, nullable=False, default=0)
    crawled_count = Column(Integer, nullable=False, default=0)
    analyzed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    ai_calls_total = Column(Integer, nullable=False, default=0)
    ai_calls_cached = Column(Integer, nullable=False, default=0)
    ai_tokens_input_total = Column(Integer, nullable=False, default=0)
    ai_tokens_output_total = Column(Integer, nullable=False, default=0)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CrawlUrl(Base):
    """One discovered/crawled URL belonging to a CrawlJob. Deduplicated per job by
    normalized URL (the SAME normalize_dedup_key the bulk scanner already uses)."""
    __tablename__ = "crawl_urls"
    __table_args__ = (
        Index("ix_crawl_urls_job_status", "crawl_job_id", "status"),
        Index("ix_crawl_urls_job_page_type", "crawl_job_id", "page_type"),
        Index("ix_crawl_urls_job_template", "crawl_job_id", "template_id"),
        Index("uq_crawl_urls_job_normalized", "crawl_job_id", "normalized_key", unique=True),
    )
    id = Column(String, primary_key=True, default=_uuid)
    crawl_job_id = Column(String, index=True, nullable=False)
    organization_id = Column(String, index=True, nullable=False)
    url = Column(String, nullable=False)
    normalized_key = Column(String, nullable=False)
    discovery_source = Column(String, nullable=False)   # seed | sitemap | internal_link
    discovery_depth = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default=URL_DISCOVERED)
    skip_reason = Column(String, nullable=True)
    http_status = Column(Integer, nullable=True)
    canonical_url = Column(String, nullable=True)
    page_type = Column(String, nullable=True)
    template_id = Column(String, nullable=True)
    content_fingerprint = Column(String, nullable=True)
    evidence = Column(JSON, nullable=True)     # deterministic evidence (Phase 4)
    analyzed = Column(Boolean, nullable=False, default=False)
    error = Column(String, nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    crawled_at = Column(DateTime, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)


class UrlFinding(Base):
    """AI Expert Analysis Engine output (Phase 5), one row per finding. `crawl_url_id`
    is set for a single-URL finding; `template_id` + `affected_url_count` carry a
    template-level finding that applies to every page sharing that template (Phase 6:
    analyze the representative page once, apply the finding across the cluster).
    `status`/`verification_status` drive the Expert Action Center (Phase 9)."""
    __tablename__ = "url_findings"
    __table_args__ = (
        Index("ix_url_findings_job_priority", "crawl_job_id", "priority"),
        Index("ix_url_findings_job_status", "crawl_job_id", "status"),
        Index("ix_url_findings_org_created", "organization_id", "created_at"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    crawl_job_id = Column(String, index=True, nullable=False)
    crawl_url_id = Column(String, index=True, nullable=True)
    organization_id = Column(String, index=True, nullable=False)
    url = Column(String, nullable=True)
    template_id = Column(String, nullable=True)
    affected_url_count = Column(Integer, nullable=False, default=1)
    page_type = Column(String, nullable=True)
    finding_title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    evidence = Column(JSON, nullable=True)
    evidence_source = Column(String, nullable=True)
    why_it_matters = Column(Text, nullable=True)
    recommended_fix = Column(Text, nullable=True)
    expected_benefit = Column(String, nullable=True)
    priority = Column(String, nullable=False, default="unknown")
    confidence = Column(String, nullable=False, default="insufficient_evidence")
    evidence_limitations = Column(Text, nullable=True)
    verification_procedure = Column(Text, nullable=True)
    status = Column(String, nullable=False, default=FINDING_OPEN)
    verification_status = Column(String, nullable=True)
    content_fingerprint = Column(String, nullable=True)   # cache/dedupe key for re-analysis
    model_used = Column(String, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    cache_hit = Column(Boolean, nullable=False, default=False)
    first_detected_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
