# Phase 2 — Completion Report

Status: **complete and verified locally** against running PostgreSQL 16 + Redis.
Scope was Phase 2 only — shipping the free scanner as a production-ready app. **No**
auth, accounts, orgs, Stripe/billing, paid plans, prompt simulation, LLM/paid APIs,
generated fixes, answer visibility, agency, or white-label work. Scanner scoring is
unchanged and the API response contract is unchanged. No LLM/paid API exists in the
free scanner path (verified by grep + code review).

Environment note (unchanged from Phase 1): this machine has **no container runtime**,
so `docker-compose.yml` / the Dockerfiles were authored and YAML/relationship-checked
but **not built/run here**. Everything else was verified against brew-run Postgres +
Redis. `AEOMirror_Developer_Tasks.md` is not present in the repo; work followed the
in-message Phase 2 spec + the Phase 0/1 docs.

---

## 1. Summary

Backend is containerized with a migrate-then-serve entrypoint, non-root user,
healthcheck, and graceful shutdown; frontend builds to static assets served by
nginx with `VITE_API_URL` as a build arg. Added `/ready`, security headers, tightened
CORS (explicit origins, never `*`), a global exception handler (no stack traces to
clients), structured JSON request logging with request IDs, in-process metrics + an
admin-gated `/debug/metrics` (with a durable scan counter), and DB-backed rubric
versioning (`is_active` + every scan records `rubric_version_id`) via a seeded
migration. The dashboard "Scans" page now lists real per-browser history and loads
stored reports via `GET /v1/scan/{id}` without regenerating.

## 2. Files modified

- `backend/app/config.py` — `log_level`, `admin_token`, `enable_hsts`
- `backend/app/main.py` — logging, request-logging + security-headers middleware, CORS tighten, exception handler, lifespan
- `backend/app/api/routes_misc.py` — `/ready`, `require_admin`, `/debug/metrics`
- `backend/app/api/routes_scan.py` — active rubric, `rubric_version_id`, metrics
- `backend/app/scanner/rubric.py` — `Rubric` dataclass + `default_rubric()`
- `backend/app/scanner/engine.py` — `score(page, rubric=None)`
- `backend/app/db/models.py` — `RubricVersion.is_active`, `Scan.rubric_version_id`
- `backend/Dockerfile` — production image (non-root, healthcheck, entrypoint)
- `backend/.env.example` — new vars documented
- `frontend/Dockerfile` — `VITE_API_URL` build arg + nginx config
- `frontend/src/App.jsx` — per-browser scan history (state, `Scans` page, selection)
- `docker-compose.yml` — full stack (Postgres + Redis + backend + frontend)
- `README.md` — Production deployment (Phase 2) section

## 3. Files created

- `backend/app/core/observability.py` — logging, metrics, request-logging + security-headers middleware
- `backend/app/scanner/rubric_provider.py` — active-rubric loader (cached, fallback)
- `backend/entrypoint.sh` — migrate + uvicorn (graceful shutdown)
- `frontend/nginx.conf` — SPA static serving (gzip, cache, headers)
- `backend/alembic/versions/01045fbbab8a_*.py` — rubric-versioning migration
- `backend/tests/test_rubric_version.py`, `backend/tests/test_observability.py`
- `PHASE_2_COMPLETION_REPORT.md`

## 4. Database migrations

- `130edb950479` — initial schema (Phase 1)
- **`01045fbbab8a`** — adds `rubric_versions.is_active` (+ index), `scans.rubric_version_id`
  (+ index); **seeds** the active rubric `2026.07.1` with the current weights; **backfills**
  existing scans (`rubric_version_id = rubric_version`). Verified upgrade → downgrade →
  re-upgrade on Postgres and SQLite; `alembic check` clean.

## 5. Environment variables

Added: `LOG_LEVEL`, `ADMIN_TOKEN`, `ENABLE_HSTS` (+ container `WEB_CONCURRENCY`,
`PORT`). All documented in `backend/.env.example` and the README env table. Secrets
are never logged; production fails fast without `DATABASE_URL` (non-SQLite) + `REDIS_URL`.

## 6. Production deployment summary

- **Backend image**: `python:3.12-slim`, non-root `appuser`, `HEALTHCHECK` on `/health`,
  `entrypoint.sh` runs `alembic upgrade head` then `uvicorn … --timeout-graceful-shutdown 20
  --workers ${WEB_CONCURRENCY:-2} --no-access-log`.
- **Frontend image**: node build (bakes `VITE_API_URL` from build arg) → nginx static
  serve (`frontend/nginx.conf`, gzip + immutable asset caching + SPA fallback + headers).
- **Compose**: Postgres + Redis (healthchecks) + backend (depends_on healthy) + frontend.
- **HTTPS**: TLS terminates at a proxy/LB; `ENABLE_HSTS=True` emits HSTS. No cookies are
  set (no auth), so "secure cookies" is N/A today.

## 7. History implementation summary

Per-browser history in `localStorage` (`aeomirror:scan_history`, capped 25, dedup by id):
`{id, url, domain, ars, rubric_version, date, status}`, appended on every successful
scan/re-scan. The `Scans` page renders it (existing table style, no redesign); clicking
**View** calls `GET /v1/scan/{id}` and shows the stored report — never regenerated. No
global list endpoint (would leak scans without auth). Live-verified: two scans listed,
selection loaded the exact stored report (1 GET, no errors).

## 8. Logging summary

`configure_logging()` sends single-line JSON to stdout at `LOG_LEVEL`. `RequestLoggingMiddleware`
stamps a `request_id`, times each request, logs `{event, request_id, timestamp, method,
path, status, latency_ms}`, and returns `X-Request-ID`. The global exception handler logs
the stack **server-side only** and returns `{detail, request_id}` (no trace to client).

## 9. Scan counter summary

`GET /debug/metrics` (admin-gated) returns a durable DB-derived counter
`scans: {today, total}` alongside the in-process metrics. No paid tooling.
Live sample: `{"scans":{"today":4,"total":4}}`.

## 10. Rubric version implementation

Weights live in `rubric_versions`; `rubric_provider.get_active_rubric()` loads the
active row (60s cache) and falls back to the code default (identical values) if the DB
has none — so scores never change. `engine.score(page, rubric)` accepts the rubric;
`create_scan` records `rubric_version_id`. Live-verified: scan stored
`rubric_version_id=2026.07.1`; unit tests confirm DB-sourced weights are honored and the
default matches code constants.

## 11. Test results

`pytest -q` → **51 passed, 1 warning** (39 from Phase 0/1 + 12 new: rubric versioning,
observability/metrics, admin gate). No tests weakened or skipped. Frontend production
build: **✓ built**. Scores unchanged (scanner tests still assert the same ARS bands).

Live verification (Postgres + Redis, backend in `ENVIRONMENT=production`):
`/health` + `/ready` ok; real scan 200 (ARS 50) stored with `rubric_version_id`;
`/debug/metrics` 404 without token / 200 with token; security headers + `X-Request-ID`
present; structured request logs emitted; dashboard history + selection verified in a
real browser.

## 12. Remaining blockers

- **No container runtime here** → Docker images/compose authored but not built/run;
  verified via brew-run Postgres/Redis instead. On a Docker host, `docker compose up
  --build` is the intended path.
- Email remains optional/unsent live (no key by design; covered by mocks in tests).

## 13. Exact deployment commands

```bash
# Build & run the full stack locally (Docker host):
docker compose up --build          # frontend :8080, backend :8000

# Frontend for a real API:
docker build --build-arg VITE_API_URL=https://api.example.com -t aeomirror-web ./frontend

# Backend (migrations run in the entrypoint); provide env at runtime:
docker run -e ENVIRONMENT=production \
  -e DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/aeomirror \
  -e REDIS_URL=redis://HOST:6379/0 \
  -e CORS_ORIGINS=https://app.example.com \
  -e ADMIN_TOKEN=... -e ENABLE_HSTS=True -p 8000:8000 aeomirror-api

# Migrations standalone (if not using the entrypoint):
npm run db:upgrade
```

## 14. Phase 3 prerequisites (NOT started)

1. **Auth/accounts/orgs** — required before any per-user server-side scan history
   or a global list endpoint (today history is per-browser to avoid leaking scans).
2. **Billing/Stripe + plan limits** — gate metered work.
3. **Paid inference** (prompt simulation, generated fixes) behind auth + quota +
   the LLM provider keys (kept out of the free path).
4. A rubric admin UI/endpoint to create/activate versions (currently a DB operation).
5. Real managed Postgres/Redis + secrets manager; run the Docker images in the target
   orchestrator; wire log/metrics shipping to a real backend.
