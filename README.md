# AEOMirror

An AI visibility platform for SEO practitioners, agencies, and site owners.
Paste a URL, get an **AI Readiness Score (ARS, 0 to 100)** measuring whether
AI systems (ChatGPT, Claude, Gemini, Perplexity) can reach, read, and cite the
site, plus a prioritized, fixable issue list.

Part of the Nextdot Mirror suite, alongside The Doc Mirror.

---

## What is in this repository

```
aeomirror-app/
├── backend/        FastAPI. The free scanner is fully built and tested.
├── frontend/       Vite + React. Marketing scanner + product dashboard.
├── docker-compose.yml
├── README.md
└── INTEGRATIONS.md  <-- what the developer must wire up (API keys, etc.)
```

**Status by layer:**

| Layer | Status |
|---|---|
| Free scanner engine (6 signal families, ARS scoring) | Built, unit-tested |
| Public scan API (SSRF guard, rate limit, cache, DB, lead capture) | Built, tested (11 passing tests) |
| Frontend (scanner wired to API + product dashboard) | Built, compiles clean |
| Auth / billing (Step 5) | Stubbed with clear integration points |
| Paid inference: prompt simulation, fix generation (Step 6) | Adapter interfaces + stubs; drop in API keys |

The free scanner is the priority and it is complete. Everything paid is
scaffolded so you only add credentials and provider calls. See INTEGRATIONS.md.

---

## Run it locally

### Local development on macOS (recommended)

Requires **Node 18+** and **Python 3.11** (`brew install python@3.11`). The
backend uses `X | None` type unions, so Python 3.10+ is mandatory.

**First-time setup** (run once):

```bash
npm install            # root tooling + frontend packages (via postinstall)
npm run setup:backend  # creates backend/.venv (Python 3.11) and installs requirements
npm run db:upgrade     # applies migrations to the configured DB (SQLite by default)
```

> Since Phase 1, the schema is created by **Alembic migrations**, not at startup —
> so `npm run db:upgrade` is required once before the first run (and after pulling
> new migrations). With the SQLite default this just initializes `backend/aeomirror.db`.

`npm run setup:backend` finds Python 3.11 (or any python3 ≥ 3.10), creates
`backend/.venv` **only if it does not already exist**, upgrades pip, and installs
`backend/requirements.txt`. It never touches your source or `.env`.

**Daily development** — one command starts both services:

```bash
npm run dev
```

This runs the FastAPI backend and the Vite frontend together via `concurrently`.
The backend command uses the virtualenv's Python directly
(`backend/.venv/bin/python -m uvicorn ...`) — no `source activate` needed. Press
**Ctrl+C once** to stop both cleanly.

**Verification URLs** (after `npm run dev`):

| What | URL |
|---|---|
| Frontend app | http://localhost:5173 |
| Backend health | http://localhost:8000/health |
| Backend API docs | http://localhost:8000/docs |

**Testing:**

```bash
npm run test:backend   # runs pytest in the backend venv (11 tests)
```

**Production frontend build:**

```bash
npm run build:frontend # outputs frontend/dist
```

**All root scripts:**

| Script | What it does |
|---|---|
| `npm run dev` | Start backend + frontend together (Ctrl+C stops both) |
| `npm run dev:backend` | Start only the FastAPI backend (venv Python, Uvicorn :8000) |
| `npm run dev:frontend` | Start only the Vite frontend (:5173) |
| `npm run setup:backend` | First-time backend venv + requirements install |
| `npm run install:frontend` | Install frontend npm packages |
| `npm run test:backend` | Run the backend test suite |
| `npm run build:frontend` | Production build of the frontend |
| `npm run services:up` / `services:down` / `services:logs` | Start/stop/tail local Postgres + Redis (Docker) |
| `npm run db:upgrade` | Apply Alembic migrations (`alembic upgrade head`) |
| `npm run db:downgrade` | Revert the last migration (`alembic downgrade -1`) |
| `npm run db:migrate` | Autogenerate a new migration (append `-- -m "msg"`) |
| `npm run loadtest` | Run the local load/abuse test against `/v1/scan` |

> The free scanner performs a **real** live scan against the backend. There is no
> silent mock fallback: if the backend is unreachable, the UI shows a clear error
> instead of fake scores. An offline demo mock exists only when
> `VITE_ENABLE_MOCK_SCANNER=true` (default `false`) in `frontend/.env`.

### Troubleshooting (macOS)

| Symptom | Fix |
|---|---|
| `[dev:backend] Backend venv missing` | Run `npm run setup:backend` |
| `TypeError: unsupported operand type(s) for \|` | Wrong Python (< 3.10). Install 3.11: `brew install python@3.11`, then delete `backend/.venv` and re-run `npm run setup:backend` |
| Frontend won't start / missing modules | Run `npm install` (root `postinstall` also installs `frontend/`), or `npm run install:frontend` |
| `Address already in use` on :8000 or :5173 | Another process holds the port: `lsof -ti :8000 :5173 \| xargs kill -9`, then `npm run dev` |
| Frontend shows "Can't reach the scanner service" | The backend isn't running. Start it with `npm run dev` (or `npm run dev:backend`) |
| Postgres connection failure | Ensure `npm run services:up` (or your local Postgres) is running and `DATABASE_URL` matches. Health at `/health` shows `database`. |
| `relation "scans" does not exist` / unapplied migrations | Run `npm run db:upgrade`. Schema is not auto-created at startup. |
| Redis connection failure | Non-fatal — the scanner falls back to in-memory and `/health` shows `redis: unavailable`. To restore shared cache/limits, start Redis and set `REDIS_URL`. |
| Missing email configuration | Expected when `RESEND_API_KEY`/`EMAIL_FROM` are blank — sends are skipped, lead capture still works. |
| `TypeError: unsupported operand ... \|` at startup / import | Wrong Python (< 3.10). See the Python fix above. |
| Port already in use (5432/6379/8000/5173) | Stop the conflicting service or `lsof -ti :PORT \| xargs kill -9`. |
| Getting HTTP 429 | Rate limit hit (`FREE_SCANS_PER_WINDOW`, default 3/hour/IP). Honor `Retry-After`, or raise the limit in `.env` for local testing. |
| Repeated identical scans look "stuck" on one result | Expected: identical URLs are served from the 24h cache (same `scan_id`, no refetch). |
| Environment variables | Backend defaults work with no `.env`; copy `backend/.env.example` → `backend/.env` to override. Frontend copies `frontend/.env.example` → `frontend/.env` (`VITE_API_URL`). **Never commit `.env`.** |

### Production infrastructure (Phase 1): Postgres, Redis, migrations, email

The free scanner is production-graded with PostgreSQL, Alembic migrations, and a
Redis-backed cache + rate limiter. For **local development** the defaults still
work with **no** Postgres/Redis (SQLite + in-memory fallback), so the quickstart
above needs nothing extra. To run the full production-like stack locally:

**1. Start Postgres + Redis** (requires Docker; healthchecked, dev-only creds):

```bash
npm run services:up      # docker-compose.services.yml -> Postgres :5432, Redis :6379
# (no Docker? install locally, e.g. `brew install postgresql@16 redis`, and run them)
```

**2. Point the backend at them** — set in `backend/.env`:

```bash
DATABASE_URL=postgresql+psycopg://aeomirror:password@localhost:5432/aeomirror
REDIS_URL=redis://localhost:6379/0
```

**3. Apply migrations** (schema is NOT auto-created at startup — Alembic owns it):

```bash
npm run db:upgrade       # alembic upgrade head
# revert last:   npm run db:downgrade
# new migration: npm run db:migrate -- -m "describe change"
```

**4. Run the app** — `npm run dev` as usual. Check
`http://localhost:8000/health` — it reports `database` and `redis` status.

**Redis behavior.** Cache and rate limiter are shared across workers via Redis
and namespaced (`aeomirror:scan-cache:*`, `aeomirror:rate-limit:*`). They **fail
open**: if Redis is unavailable the scanner keeps working (per-process fallback)
and never returns fake data. Rate-limited requests get **HTTP 429 + Retry-After**.

**Email (lead welcome, Resend).** Optional. Leave blank to disable (lead capture
still works). To enable, set in `backend/.env`:

```bash
RESEND_API_KEY=your_key         # never commit real keys
EMAIL_FROM=AEOMirror <hi@aeomirror.com>
APP_BASE_URL=http://localhost:5173
```

Sending is best-effort: a failed send never fails the scan or the lead write, and
a welcome email is sent only on first capture.

**Production requires real infra.** With `ENVIRONMENT=production`, the app fails
fast at startup unless `DATABASE_URL` (non-SQLite) and `REDIS_URL` are set.

**Load / abuse test** (local only; needs the server running with `TRUST_PROXY=True`
to exercise per-identity limits):

```bash
LOADTEST_BASE_URL=http://localhost:8000 npm run loadtest
```

It reports total/success/cached/429/failures and p50/p95 latency, and verifies
cache dedupe + 429 + Retry-After.

### Option A: Docker (full stack)

The root `docker-compose.yml` runs the complete production-like stack: Postgres +
Redis + backend (which applies Alembic migrations on start) + frontend.

```bash
docker compose up --build
# frontend: http://localhost:8080
# backend:  http://localhost:8000  (docs at /docs)
```

Credentials in the compose file are dev-only placeholders. `VITE_API_URL` is a
frontend build arg (the static build bakes it in). See "Production deployment"
below for real deployments.

### Option B: run each service directly

**Backend**
```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
# API docs: http://localhost:8000/docs
```

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env      # VITE_API_URL defaults to http://localhost:8000
npm run dev
# http://localhost:5173
```

With the backend up, the free scanner performs a real live scan. If the backend
is not running, the frontend shows a clear error (no fake data). An offline demo
mock is available only when `VITE_ENABLE_MOCK_SCANNER=true` in `frontend/.env`.

---

## Production deployment (Phase 2)

The free scanner ships as a production app: containerized backend + frontend,
HTTPS-ready behind a proxy, migrations on deploy, observability, and DB-versioned
rubrics.

### Required environment variables

| Variable | Where | Purpose | Prod-required |
|---|---|---|---|
| `ENVIRONMENT` | backend | `development` \| `production` \| `test` | — |
| `DATABASE_URL` | backend | PostgreSQL URL (`postgresql+psycopg://…`) | **yes** (non-SQLite) |
| `REDIS_URL` | backend | Shared cache + rate limiter | **yes** |
| `CORS_ORIGINS` | backend | Comma-separated **exact** frontend origins (never `*`) | yes |
| `LOG_LEVEL` | backend | `DEBUG`/`INFO`/`WARNING`/`ERROR` | no (default INFO) |
| `ADMIN_TOKEN` | backend | Guards `/debug/*` (required in prod to reach it) | recommended |
| `ENABLE_HSTS` | backend | Emit HSTS (only when served over HTTPS) | no |
| `RESEND_API_KEY`, `EMAIL_FROM`, `APP_BASE_URL` | backend | Lead welcome email (optional) | no |
| `CACHE_TTL_SECONDS` | backend | Cache dedupe window (default 24h) | no |
| `FREE_SCANS_PER_WINDOW`, `RATE_LIMIT_WINDOW_SECONDS` | backend | Rate limits | no |
| `FETCH_TIMEOUT_SECONDS`, `FETCH_CONNECT_TIMEOUT_SECONDS`, `FETCH_MAX_BYTES`, `FETCH_MAX_REDIRECTS`, `FETCH_RETRY_COUNT` | backend | Fetch hardening | no |
| `TRUST_PROXY` | backend | Honor `X-Forwarded-For` (only behind a trusted proxy) | no |
| `WEB_CONCURRENCY`, `PORT` | backend | Uvicorn workers / port (container) | no |
| `VITE_API_URL` | frontend | API base URL, **baked in at build time** | yes |

Full list with defaults: `backend/.env.example`. Never commit real secrets.

### Migrations (run on every deploy, before/at startup)

```bash
npm run db:upgrade      # alembic upgrade head   (the backend image also runs this on start)
npm run db:downgrade    # alembic downgrade -1   (rollback one revision)
npm run db:migrate -- -m "describe change"   # autogenerate a new migration
```

Schema is owned by Alembic — the app never creates tables at startup.

### Frontend build

```bash
VITE_API_URL=https://api.example.com npm run build:frontend   # or the Docker build arg
# docker build --build-arg VITE_API_URL=https://api.example.com ./frontend
```

`VITE_API_URL` is compiled into the static bundle, so rebuild when it changes.

### Frontend API URL configuration (Vercel)

The frontend reads the backend URL **only** from `import.meta.env.VITE_API_URL` —
there is no hardcoded URL anywhere in the code.

**Local development** (works out of the box — no manual env editing):

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

`frontend/.env.development` (committed) already sets
`VITE_API_URL=http://localhost:8000`, and Vite loads it automatically in dev mode.
To point your local frontend at a different backend, create `frontend/.env.local`
(gitignored) and override it:

```bash
# frontend/.env.local
VITE_API_URL=http://localhost:8000
```

**Production (Vercel)** — do NOT commit the production URL; supply it in the dashboard:

1. Vercel project → **Settings → Environment Variables**
2. Add: `VITE_API_URL = https://api.aeomirror.com` (Production scope)
3. **Redeploy.** Vite injects env vars at **build time**, so a change to a Vercel
   environment variable only takes effect after a new build/deploy.

Because `.env.development` is dev-only, it can never leak `localhost` into a Vercel
production build — production always uses the dashboard value.

### Backend deployment

The backend image (`backend/Dockerfile`) runs as a non-root user, has a
`HEALTHCHECK` on `/health`, and its entrypoint applies migrations then starts
Uvicorn with graceful shutdown (`--timeout-graceful-shutdown 20`, `WEB_CONCURRENCY`
workers). Put it behind a TLS-terminating proxy/load balancer (set `ENABLE_HSTS=True`
there). Endpoints: `/health` (liveness), `/ready` (readiness — gates on the DB).

### Rollback

- **App:** redeploy the previous image tag.
- **Database:** `npm run db:downgrade` (one revision) — each migration ships a
  tested `downgrade()`.
- **Rubric:** activate a previous row in `rubric_versions` (see below); scores use
  the active version within ~60s, no deploy needed.

### Monitoring & logs

- Structured JSON request logs on stdout: `request_id`, `timestamp`, `method`,
  `path`, `status`, `latency_ms`. Errors log a full stack trace **server-side only**
  with the `request_id`; clients only get a generic message + id.
- `GET /debug/metrics` (admin-gated) returns totals: scans, cache hit/miss,
  429 count, average scan latency, plus a durable scan counter (`today`/`total`)
  and the active rubric version.
  ```bash
  curl -H "X-Admin-Token: $ADMIN_TOKEN" https://api.example.com/debug/metrics
  ```

### Scan history (dashboard)

The dashboard "Scans" page lists this browser's past scans (date, URL, ARS, rubric
version, status) and, on click, re-fetches the stored report from
`GET /v1/scan/{id}` — it never regenerates. History is per-browser (there is no
auth yet, so there is no global list endpoint that would leak other users' scans).

### Rubric version management

Rubric weights live in the `rubric_versions` table. The active row (seeded as
`2026.07.1`) is loaded by the scanner (cached ~60s), and every scan records its
`rubric_version_id`, so historical scores stay comparable. To roll out new weights,
insert a new row and flip `is_active` to it (deactivating the old one). No code
deploy is required.

---

## Try it

Backend, live scan of any public URL:
```bash
curl -X POST http://localhost:8000/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Frontend: open the app, toggle "Homepage scanner", paste a URL, run a scan.
A second scan triggers the email-capture gate.

---

## Run the tests

```bash
cd backend
pip install -r requirements.txt
pytest -q
```

Covers the scanner logic and the full API path (SSRF, rate limit, cache,
scoring, persistence, retrieval, lead capture).

---

## Architecture (free scanner)

```
client → POST /v1/scan
  → rate limit (per IP)
  → SSRF validation (reject private / loopback hosts)
  → cache check (24h dedupe by normalized URL)
  → fetch (HTML + robots.txt + llms.txt + sitemap, size/timeout capped)
  → score (6 deterministic signal families → ARS + issue list)
  → persist (Postgres/SQLite) + cache
  → JSON response
```

**The one rule that must not break:** nothing in the free scanner calls an LLM
or a paid API. Every check is a fetch, a parse, or a rule. That is what lets
the free scan run on unlimited traffic at near-zero marginal cost. Anything
that needs a model lives behind the paywall (Step 6).

---

## Production checklist (short)

See INTEGRATIONS.md for the full version. In brief:
1. Swap SQLite for Postgres (`DATABASE_URL`).
2. Swap in-memory cache/rate-limit for Redis (`REDIS_URL`).
3. Wire the email provider (lead capture).
4. Implement auth (Step 5) and billing.
5. Implement the paid inference adapters (Step 6): plug in OpenAI / Anthropic /
   Google / Perplexity keys.
6. Move DB setup from `create_all` to Alembic migrations.
