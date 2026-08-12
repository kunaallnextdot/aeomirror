"""ORM models for the free-scanner slice. The full product schema is in the PRD."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String

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


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    # The job queue. Claim-based so a future distributed worker can process it:
    # a worker atomically flips pending -> running, then completed/failed. At most
    # one active (pending/running) job per monitor is enforced in the scheduler.
    __table_args__ = (
        Index("ix_jobs_claimable", "status", "run_after"),
        Index("ix_jobs_monitor_status", "monitor_id", "status"),
    )
    id = Column(String, primary_key=True, default=_uuid)
    # Monitor jobs reference a monitor; site_scan jobs reference a scan instead, so
    # both are nullable and the worker dispatches on `kind`.
    monitor_id = Column(String, index=True, nullable=True)
    scan_id = Column(String, index=True, nullable=True)          # set for kind="site_scan"
    organization_id = Column(String, index=True, nullable=True)
    kind = Column(String, nullable=False, default="scheduled")   # scheduled|manual|site_scan
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
