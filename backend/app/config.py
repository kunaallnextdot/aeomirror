"""Central configuration. All values are environment-overridable.

The defaults are safe for LOCAL DEVELOPMENT only (SQLite, in-memory cache and
rate limiter, private hosts blocked). Production-required values (a real
DATABASE_URL and REDIS_URL) are validated at startup when ENVIRONMENT=production;
missing ones fail fast with a clear error. Secrets are never logged.

See INTEGRATIONS.md (A1 Postgres, A3 Redis, B1 Email) and PHASE_1_*.md.
"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---
    app_name: str = "AEOMirror API"
    environment: str = "development"  # development | production | test
    # Source of truth for the database. Safe SQLite default for local dev; set a
    # real postgresql+psycopg:// URL in production (validated below).
    database_url: str = "sqlite:///./aeomirror.db"

    # --- SQLAlchemy pool (used for non-sqlite engines) ---
    db_pool_size: int = 5           # small v1 footprint
    db_max_overflow: int = 5        # burst headroom
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800     # recycle connections every 30 min

    # --- fetch limits ---
    fetch_timeout_seconds: int = 12          # total request budget
    fetch_connect_timeout_seconds: int = 5   # connection establishment budget
    fetch_max_bytes: int = 3_000_000         # 3 MB response cap
    fetch_max_redirects: int = 5
    user_agent: str = "AEOMirrorBot/1.0 (+https://aeomirror.com/bot)"

    # --- fetch retry (transient failures only; keeps scans fast) ---
    fetch_retry_count: int = 2               # attempts AFTER the first try
    fetch_retry_backoff_base: float = 0.2    # seconds; exponential base
    fetch_retry_backoff_max: float = 2.0     # cap per-attempt sleep

    # --- bulk scan ---
    # A bulk scan takes up to N user-provided URLs (pasted or via CSV/XLSX upload) and
    # scores them all in one background job, producing an aggregate report. Page
    # selection comes from the user (no sitemap discovery). Every URL is still
    # SSRF-validated before fetch; per-URL failures are recorded and excluded.
    bulk_max_urls: int = 50                    # max URLs accepted per bulk scan
    bulk_concurrency: int = 5                  # simultaneous page fetches
    bulk_page_timeout_seconds: int = 12        # per-URL wall-clock cap (reuses the fetch budget)
    bulk_total_budget_seconds: int = 600       # whole-job wall-clock budget; finalize partial with truncated=true
    # --- Change attribution (scan snapshots) ---
    snapshot_retention_days: int = 90           # delete snapshots older than this (keeps each active
                                                # monitor's most recent snapshot regardless of age)

    # --- AI crawler access check (runs on single-page + monitor scans, not bulk) ---
    crawler_access_enabled: bool = True         # issue live per-UA GETs to detect robots/WAF blocks
    crawler_access_timeout_seconds: int = 10    # per-request timeout
    crawler_access_concurrency: int = 6         # bounded concurrency for the per-bot requests

    bulk_upload_max_bytes: int = 2_000_000     # hard cap on an uploaded URL-list file (read in bounded
                                               # chunks; zip-container formats like .xlsx can decompress
                                               # far larger, so we never buffer past this)

    # --- security ---
    # Blocks scans of localhost / private IP ranges (SSRF protection).
    # Set True ONLY in tests so a local fixture server can be scanned.
    allow_private_hosts: bool = False
    # Only honor X-Forwarded-For when explicitly behind a trusted proxy.
    trust_proxy: bool = False

    # --- cache ---
    cache_ttl_seconds: int = 86_400          # 24h dedupe for identical URLs
    # redis_url: set in prod to switch from in-memory to shared cache + limiter
    redis_url: str | None = None

    # --- rate limiting (abuse protection on POST /v1/scan) ---
    # NOT a billing limit — scanning requires an account and is metered by scan-job
    # quota (see entitlements). This is a modest per-IP ceiling purely to blunt abuse
    # (bursts / scripted hammering). Also reused by login/contact limiters.
    free_scans_per_window: int = 20
    rate_limit_window_seconds: int = 3600    # per rolling hour, per IP

    # --- CORS ---
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- observability / ops ---
    log_level: str = "INFO"                   # DEBUG | INFO | WARNING | ERROR
    # Protects the /debug/* admin endpoints. If unset, they are reachable only in
    # non-production; in production a token is required.
    admin_token: str | None = None
    # Emit HSTS header (only when the app is served over HTTPS behind a proxy).
    enable_hsts: bool = False
    # Optional error tracking (Sentry). Left unset -> disabled.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    # Interactive API docs (/docs, /redoc). Disabled in production by default so the
    # schema isn't publicly browsable; override with expose_docs=True if desired.
    expose_docs: bool = False

    # --- email (lead welcome + auth) ---
    resend_api_key: str | None = None
    email_from: str | None = None            # e.g. "AEOMirror <hi@aeomirror.com>"
    app_base_url: str = "http://localhost:5173"
    # API's own public base URL — used for links the API itself serves (the digest
    # unsubscribe confirmation page), distinct from the frontend app_base_url.
    api_base_url: str = "http://localhost:8000"
    # Weekly digest email backend: "resend" | "console". When unset, defaults to console
    # outside production so local dev logs the rendered email instead of calling Resend.
    # Scopes ONLY the digest + preflight (auth/billing/summary email is unchanged).
    email_backend: str | None = None
    # Weekly digest send window (fixed UTC — per-org timezone is out of scope). 0 = Monday.
    digest_send_weekday: int = 0
    digest_send_hour_utc: int = 13

    # --- email transport: Gmail SMTP (Contact & Support) ---
    # Credentials come ONLY from the environment (never hardcoded). Use a Gmail
    # App Password (not the account password). Same architecture as The Doc Mirror.
    gmail_user: str | None = None            # the authenticated Gmail address
    gmail_app_password: str | None = None    # 16-char Gmail App Password
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587                     # STARTTLS
    smtp_timeout_seconds: int = 15
    # Mailbox that receives contact-form notifications (defaults to gmail_user).
    contact_email: str | None = None

    # --- contact form abuse protection ---
    contact_max_per_window: int = 5          # submissions per window, per IP
    contact_rate_window_seconds: int = 3600  # rolling hour
    contact_message_min_len: int = 20
    contact_message_max_len: int = 5000

    # --- AI features (Anthropic) — server-side ONLY ---
    # Powers (A) the AI-written narrative on PAID reports and (B) the Pro-only AI
    # content-quality insights. The key comes ONLY from the environment; it is never
    # logged and never sent to the frontend. When unset, `ai_enabled` is False and both
    # features fall back to the deterministic rule-based path (no behaviour change).
    anthropic_api_key: str | None = None
    ai_model: str = "claude-haiku-4-5-20251001"   # fast, low-cost; overridable via env
    ai_max_tokens: int = 1500
    ai_timeout_seconds: int = 30
    # Cost + abuse guardrails for the AI narrative (now available on ALL plans):
    ai_free_monthly_narratives: int = 2     # per-org AI narratives / calendar month (Free tier)
    ai_global_daily_calls: int = 500        # hard ceiling on AI narrative calls / day, all orgs
    ai_require_verified_email_for_free: bool = True  # Free tier needs a verified email
    # Hard overall wall-clock budget for the interactive "Analyze content" endpoint
    # (page re-fetch + AI analysis). Past this the request returns 504 instead of
    # running long enough for an upstream proxy to sever the connection. Sized to fit
    # the AI call WITH its one retry: 2 × content_insight_ai_timeout_seconds + backoff
    # (~41s) plus a normal cached re-fetch, with headroom under a ~60s proxy limit.
    content_insight_budget_seconds: int = 55
    # Per-attempt AI timeout for the INTERACTIVE content-insight path only. Shorter than
    # ai_timeout_seconds (which the report-narrative path keeps) so a retry fits the
    # budget above — and so any threadpool call orphaned by a budget timeout self-
    # terminates quickly instead of burning tokens after we've returned 504.
    content_insight_ai_timeout_seconds: int = 20

    # --- Phase 9: billing + subscriptions ---
    # Master switch for plan enforcement. When False (e.g. tests), every org has
    # full access regardless of plan — subscriptions/payments still work, only the
    # gating is bypassed. Production/dev default is True.
    billing_enforced: bool = True
    payment_provider: str = "stripe"            # stripe | razorpay | paddle (future)
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None    # signs incoming webhooks
    stripe_price_pro: str | None = None         # Stripe price id for the Pro plan
    billing_currency: str = "usd"
    pro_price_cents: int = 2900                 # $29 / month
    report_price_cents: int = 900               # $9 one-time full report
    # Plan limits. A "scan job" is one user-initiated scan (single-page today; a
    # bulk-50 scan counts as one job too). Scans executed automatically by monitors do
    # NOT consume scan-job quota (only the API path meters them). Scans + comparisons
    # are per calendar month; monitors is a concurrent cap. Pro is metered too (15
    # scan jobs / month), NOT unlimited. The per-IP abuse limiter above is separate.
    free_monthly_scan_jobs: int = 1     # Free: one scan job per calendar month
    pro_monthly_scan_jobs: int = 15     # Pro: 15 scan jobs per calendar month
    free_history_limit: int = 10        # how many recent scans stay visible (Free)
    free_monitor_limit: int = 1         # concurrent monitors a Free org may have
    pro_monitor_limit: int = 10         # concurrent monitors a Pro org may have
    free_monthly_compares: int = 1      # scan comparisons per calendar month (Free)
    free_share_limit: int = 3           # concurrent active public share links a Free org may have (Pro: unlimited)

    # --- Public report share links ---
    # Shareable read-only report links (/r/<token>). Token is opaque; only its sha256
    # hash is stored. Links expire after this TTL and can be revoked. The public read
    # path is rate-limited per IP and never regenerates the report or its AI narrative.
    report_share_ttl_seconds: int = 7_776_000   # 90 days (agency → client sharing horizon)
    public_report_include_ai: bool = True       # include the (stripped) persisted AI narrative in public payloads
    public_report_max_per_window: int = 30      # per-IP reads allowed per window (anti-scraping)
    public_report_rate_window_seconds: int = 60

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key)

    # --- Phase 8: admin platform ---
    # Comma-separated emails auto-granted platform-admin access (in addition to any
    # user with is_platform_admin=True). Lets you bootstrap the first admin.
    admin_emails: str = ""

    # --- Phase 7: monitoring + scheduler ---
    # In-process background worker that runs due scheduled scans. Disabled in
    # tests (which drive the scheduler directly for determinism) and can be turned
    # off to run scans from a separate worker process instead (future scaling).
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30      # how often the worker ticks
    scheduler_batch_size: int = 5             # max jobs drained per tick
    monitor_job_max_attempts: int = 3         # retries for a failed scan job
    monitor_job_retry_backoff_seconds: int = 120
    # Alert thresholds (overall + per-category score drops).
    alert_score_drop_warning: int = 8
    alert_score_drop_critical: int = 15
    alert_category_regression: int = 15       # per-category drop that alerts
    alert_critical_score: int = 25            # a signal at/below this is "critical"

    # --- Phase 5: authentication ---
    # HS256 signing secret for JWT access tokens. A safe random dev default is
    # generated per-process; production MUST set a stable, secret value (validated
    # below) so tokens survive restarts and can't be forged.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900            # 15 min access token
    refresh_token_ttl_seconds: int = 604_800       # 7 days (normal login)
    refresh_token_remember_seconds: int = 2_592_000  # 30 days ("remember me")
    verify_token_ttl_seconds: int = 86_400         # 24h email-verification link
    reset_token_ttl_seconds: int = 3_600           # 1h password-reset link
    invite_token_ttl_seconds: int = 604_800        # 7 days invitation link
    # Refresh-token cookie. Secure=True in production (HTTPS only). SameSite=strict
    # blocks cross-site sends, which (with Bearer-auth mutations) covers CSRF.
    auth_cookie_name: str = "aeomirror_refresh"
    auth_cookie_secure: bool = False               # forced True in production below
    auth_cookie_samesite: str = "strict"           # strict | lax | none
    auth_cookie_domain: str | None = None
    # Login abuse protection (per email+IP, separate from the scan limiter).
    login_max_attempts: int = 8
    login_window_seconds: int = 900                # 15 min lockout window

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def uses_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key and self.email_from)

    @property
    def digest_email_backend(self) -> str:
        """Resolved digest email backend: an explicit EMAIL_BACKEND wins, else 'console'
        outside production (logs to stdout), 'resend' in production."""
        if self.email_backend in ("resend", "console"):
            return self.email_backend
        return "resend" if self.is_production else "console"

    @property
    def ai_enabled(self) -> bool:
        """True when an Anthropic key is configured. Both AI features check this and
        degrade to the rule-based path when it is False."""
        return bool(self.anthropic_api_key)

    @property
    def smtp_configured(self) -> bool:
        """True when Gmail SMTP can send (Contact & Support emails)."""
        return bool(self.gmail_user and self.gmail_app_password)

    @property
    def smtp_from(self) -> str | None:
        """From address for SMTP mail: EMAIL_FROM if set, else the Gmail user."""
        return self.email_from or self.gmail_user

    @property
    def contact_recipient(self) -> str | None:
        """Where contact-form notifications land: CONTACT_EMAIL, else the Gmail user."""
        return self.contact_email or self.gmail_user

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """Fail fast in production if the shared infra is not configured.
        Never include secret values in the message."""
        if self.is_production:
            missing: list[str] = []
            if self.uses_sqlite:
                missing.append("DATABASE_URL (must be a non-SQLite URL in production)")
            if not self.redis_url:
                missing.append("REDIS_URL (required for shared cache + rate limiting)")
            if not self.jwt_secret:
                missing.append("JWT_SECRET (required to sign auth tokens)")
            if missing:
                raise ValueError(
                    "Invalid production configuration. Set: " + "; ".join(missing)
                )
            # Never ship insecure auth cookies over HTTP in production.
            self.auth_cookie_secure = True
        # Non-production convenience: a per-process random secret so local dev works
        # with zero config. It changes on restart (existing tokens invalidate) — that
        # is fine for dev and never used in production (validated above).
        if not self.jwt_secret:
            import secrets
            self.jwt_secret = secrets.token_urlsafe(48)
        return self


settings = Settings()
