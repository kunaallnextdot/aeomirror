# Phase 1 — Completion Report

Status: **complete and verified locally** against a running PostgreSQL 16 and
Redis. Scope was Phase 1 only (production-grading the free scanner). No Phase 2
deployment work, and no auth/billing/paid-inference/LLM work was done. The free
scanner path calls no LLM or paid API.

Verification note: this machine had **no container runtime** (Docker/Podman/
Colima absent) and no DB/cache installed. To verify honestly, PostgreSQL 16 and
Redis were installed via Homebrew and run as local processes. `docker-compose.services.yml`
+ the `services:*` scripts are provided for the team's Docker workflow but were
**not executed here** (no Docker); everything else was executed against the
brew-run services.

---

## Architecture changes

- **Database:** SQLite remains the dev/test default; production uses PostgreSQL via
  `DATABASE_URL` (psycopg 3, `postgresql+psycopg://`). Engine now uses a bounded
  pool (`pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`) and
  `pool_pre_ping=True` for non-SQLite engines. (`app/db/session.py`, `app/config.py`)
- **Schema ownership:** startup no longer calls `init_db()`/`create_all()`.
  Alembic owns the schema. (`app/main.py`, `alembic/`)
- **Cache + rate limiter:** Redis-backed when `REDIS_URL` is set, shared across
  workers, namespaced, JSON-serialized (no pickle). Both **fail open** to a
  per-process in-memory implementation on Redis error. (`app/core/cache.py`)
- **Rate limiting:** atomic Redis `INCR`+`EXPIRE` (Lua), returns 429 +
  `Retry-After`. Client identity honors `X-Forwarded-For` only when `TRUST_PROXY=True`.
  (`app/core/cache.py`, `app/api/routes_scan.py`)
- **Email:** real Resend integration, best-effort, HTML-escaped, deduped to first
  capture; lead is stored before sending. (`app/services/leads.py`)
- **Fetch hardening:** manual redirect following with **per-hop SSRF revalidation**,
  split connect/total timeouts, streamed body cap, bounded exponential backoff +
  jitter retrying transient failures only (502/503/504, connect/read errors).
  (`app/core/fetch.py`)
- **Config:** centralized + validated; production fails fast without a real
  `DATABASE_URL`/`REDIS_URL`; secrets never logged. (`app/config.py`)
- **Health:** `/health` now reports `database` and `redis` status.

## Database migration status

- Alembic initialized; env wired to the app's `DATABASE_URL` + `Base.metadata`.
- Initial migration **`130edb950479`** ("initial schema") creates `scans`,
  `leads`, `rubric_versions` + index `ix_scans_normalized_url`.
- Verified against Postgres: empty → `upgrade head` (tables created) → `current`
  shows head → `downgrade base` (dropped) → `upgrade head` (recreated).
- `alembic check` → "No new upgrade operations detected" (models ↔ migration in sync).
- `tests/test_migrations.py` re-verifies upgrade+downgrade on throwaway SQLite.

## Redis cache behavior

- Namespaced key `aeomirror:scan-cache:<sha256(normalized-url)>`, TTL 24h, JSON.
- Verified live: 3 identical `POST /v1/scan` for example.com returned the **same
  `scan_id`** and Postgres held exactly **1** `scans` row (no refetch/re-persist).
- Fail-open verified: with Redis **shut down**, a scan returned real data
  (ars=50, `_mock=false`) and `/health` reported `redis: unavailable`, `status: ok`.

## Redis rate-limiter behavior

- Atomic INCR+EXPIRE; namespaced `aeomirror:rate-limit:free-scan:<client-id>`.
- Verified live: 4th request in the window → **HTTP 429** with **`Retry-After: 3600`**
  and a meaningful body ("Free scan limit reached…").
- Shared-state test (`test_rate_limit.py`) uses real Redis: two limiter instances
  (= two workers) share one counter.

## Email integration

- Resend via `httpx`; best-effort. Tests cover success, provider failure, missing
  config (no call), exception-swallow, HTML escaping, and **lead stored even when
  email fails**. No real emails are sent in tests. Locally email is disabled
  (blank keys) and lead capture was verified to persist to Postgres.

## Fetch hardening

`tests/test_fetch.py` (httpx MockTransport, no network) — all pass:
- SSRF rejects localhost / 127.0.0.1 / 10.0.0.0/8 / 169.254.169.254 / 192.168.0.0/16
- redirect to a private IP is blocked **and not retried**
- non-http(s) scheme rejected
- oversized body capped
- excessive redirects → error
- read timeout → `FetchError` (after bounded retry)
- transient 502 retried once then succeeds

## Load-test results (local, TRUST_PROXY=True, target example.com)

```
scenario A (one identity, 6 requests): 1 cold + 2 cache-hits (same scan_id) + 3×429 (Retry-After 3600)
scenario B (25 concurrent, unique identities): 25×200, all cache hits, 0 failures
TOTAL: 31 requests | success 28 | cached 27 | 429 3 | failures 0
p50 39.1 ms | p95 50.5 ms | RESULT: PASS (cache dedupe + 429 + Retry-After verified)
```

## Backend tests

`pytest -q` → **39 passed, 1 warning** (11 pre-existing + 28 new). No tests were
weakened or skipped to pass. The pre-existing `on_event` deprecation warning is
gone (startup handler removed).

## Frontend build

`npm run build:frontend` → **✓ built** (dist emitted). Phase 0 behavior unchanged
(no frontend redesign; API response contract unchanged).

## Unresolved blockers

- **No container runtime on this machine** — `docker-compose.services.yml` and the
  `services:*` scripts are provided but were not executed here; local verification
  used brew-run Postgres/Redis instead. On a Docker-equipped machine,
  `npm run services:up` is the intended path.
- Email path is integrated but **not sent end-to-end to Resend** (no API key by
  design); verified via mocks + the missing-config skip.

## Phase 2 prerequisites (deployment — NOT started)

1. Managed Postgres + Redis (or the compose file promoted to real infra), with
   `DATABASE_URL`/`REDIS_URL` provided via secrets.
2. `alembic upgrade head` as a deploy step (startup no longer creates schema).
3. Real `RESEND_API_KEY`/`EMAIL_FROM` (+ verified sending domain).
4. Set `TRUST_PROXY=True` only behind a trusted proxy that sets `X-Forwarded-For`.
5. Run behind multiple workers to exploit the now-shared Redis cache/limits.
6. Container/image build + orchestration (the existing root `docker-compose.yml`
   builds the app images) — this is Phase 2 territory.
