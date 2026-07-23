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

    # --- rate limiting (free, anonymous scans) ---
    free_scans_per_window: int = 3
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

    # --- email (lead welcome) ---
    resend_api_key: str | None = None
    email_from: str | None = None            # e.g. "AEOMirror <hi@aeomirror.com>"
    app_base_url: str = "http://localhost:5173"

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def uses_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key and self.email_from)

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
            if missing:
                raise ValueError(
                    "Invalid production configuration. Set: " + "; ".join(missing)
                )
        return self


settings = Settings()
