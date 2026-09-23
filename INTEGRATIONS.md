# INTEGRATIONS.md

AEOMirror is fully built through Phase 10 — scanner, auth, billing, monitoring,
reports, admin, and the AI features are all implemented and tested. **Nothing in the
product is stubbed anymore**; the experimental inference module was removed in Phase 10.

What's left is **configuration**: point the app at production infrastructure and drop in
provider secrets. Local dev works out of the box with none of these (SQLite + in-memory
cache/rate-limiter + dev-completion checkout + AI disabled). Set secrets in your host's
dashboard (Render / Vercel), never in Git.

---

## A. Production infrastructure

### A1. PostgreSQL
- **Var:** `DATABASE_URL` (backend) — e.g. `postgresql+psycopg://user:pass@host:5432/aeomirror`
- Dev falls back to SQLite. Migrations run on deploy (`alembic upgrade head`, via
  `entrypoint.sh`); the ORM is already Postgres-compatible.

### A2. Redis (shared cache + rate limiter)
- **Var:** `REDIS_URL` (backend)
- Dev uses a per-process in-memory fallback. With more than one worker, set `REDIS_URL`
  so the 24h scan cache and the per-IP rate limiter are shared. Both fail open.

### A3. JWT secret
- **Var:** `JWT_SECRET` (backend) — **required in production** (startup fails without it).
  Dev generates a per-process secret.

---

## B. Provider secrets (each feature degrades gracefully without its key)

### B1. Stripe (billing)
- **Vars:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`
- Billing is fully implemented: plans, checkout, signature-verified webhooks at
  `/billing/webhooks/stripe`, subscription lifecycle, invoices, and entitlement gating.
  Without keys the checkout runs in **dev-completion** mode (a local completion page) so
  the flow is exercisable without Stripe. Set the keys for real payments; live mode also
  needs a configured Stripe customer portal.

### B2. Resend (transactional email)
- **Vars:** `RESEND_API_KEY`, `EMAIL_FROM`
- Auth / billing / monitoring emails are wired (best-effort, logged). Leave unset to
  disable sending — everything else still works.

### B3. Anthropic (AI report narrative + content insights)
- **Vars:** `ANTHROPIC_API_KEY` (optionally `AI_MODEL`, `AI_MAX_TOKENS`, `AI_TIMEOUT_SECONDS`)
- Built: **(A)** a Claude-written report narrative on **all plans**, tiered — paid
  viewers (Pro orgs or a $9-unlocked report) get full insights + action plan, the free
  tier gets the summary + top-3 insights (subject to the free guardrails).
  `entitlements.export_unlocked` selects the TIER, it does not gate access; the narrative
  is cached on the `reports` row. **(B)** Pro-only per-page AI Content Insights (cached per `(scan, page)` in
  `ai_content_insights`). The key is read only from env, never logged and never sent to
  the frontend. Unset or on failure → the deterministic rule-based path. `pip install -r
  requirements.txt` already includes `anthropic`.

### B4. Sentry (optional error tracking)
- **Vars:** `SENTRY_DSN` (+ `SENTRY_TRACES_SAMPLE_RATE`); uncomment `sentry-sdk` in
  `requirements.txt`. Unset → disabled.

### B5. Admin access
- **Vars:** `ADMIN_EMAILS` (auto-grant platform-admin to these emails), `ADMIN_TOKEN`
  (guards `/metrics` and `/debug/*` in production).

### B6. AEO Answer Simulator (optional AI explanation step)
- **Vars:** `ANSWER_SIMULATOR_ENABLED`, `ANSWER_SIMULATOR_PROVIDER` (`local`|`anthropic`|`none`),
  `ANSWER_SIMULATOR_BASE_URL`, `ANSWER_SIMULATOR_MODEL`, `ANSWER_SIMULATOR_TIMEOUT_SECONDS`,
  `ANSWER_SIMULATOR_MAX_EVIDENCE_UNITS` — all optional, documented inline in
  `backend/.env.example`.
- The AEO Answer Simulator (Answer Tracking's primary flow) is deterministic and makes
  **zero external calls by default** — it retrieves evidence from a site's own already-
  scanned content and scores answerability with no LLM involved. Unset →
  `ANSWER_SIMULATOR_ENABLED=false`, the true $0 default. The optional "Explain why" AI
  step (Pro-only) can be pointed at either a self-hosted Ollama-compatible endpoint
  (`ANSWER_SIMULATOR_PROVIDER=local`; Render's instances are not assumed to have a GPU,
  so this is realistically dev/self-hosted-only) or the same `ANTHROPIC_API_KEY` already
  used for the report narrative (`ANSWER_SIMULATOR_PROVIDER=anthropic`, a small per-call
  cost). No new secret to manage either way.

---

## C. Frontend

### C1. API URL
- **Var:** `VITE_API_URL` (frontend / Vercel) → your deployed API (e.g.
  `https://api.aeomirror.com`). Baked in at build time; rebuild after changing it.

### C2. Optional analytics
- **Vars:** `VITE_GA_ID`, `VITE_CLARITY_ID`, `VITE_GSC_VERIFICATION`. Nothing loads
  unless set.

### C3. SEO assets
- Add `frontend/public/og-image.png` and `logo.png` (referenced by the SEO / JSON-LD tags).

---

## D. Deployment

`render.yaml` (backend API + a separate monitoring worker + PostgreSQL 16 + Redis) and
`frontend/vercel.json` (SPA routing + security headers / CSP) are authored and ready; CI
(`.github/workflows/ci.yml`) gates both auto-deploys. See `DEPLOYMENT.md`, `RUNBOOK.md`,
`OPERATIONS.md`, `BACKUP.md`, and `SECURITY.md`.

---

## Quick reference: real vs. needs-config today

| Real and working (code complete) | Needs configuration only |
|---|---|
| 10-signal scanner + weighted score | `DATABASE_URL` (Postgres) — dev uses SQLite |
| Public + org-scoped scan API, SSRF, cache, rate limit | `REDIS_URL` — dev uses in-memory |
| Auth (register / login / refresh-rotation / reset / verify / RBAC) | `JWT_SECRET` (required in prod) |
| Billing (plans, checkout, signed webhooks, invoices, gating) | Stripe keys (dev-completion works without) |
| Monitoring (scheduler / worker, alerts, trends) | — |
| Reports + PDF / CSV / JSON exports (gated) | — |
| AI report narrative + Content Insights | `ANTHROPIC_API_KEY` |
| Transactional email (auth / billing / monitoring) | `RESEND_API_KEY` + `EMAIL_FROM` |
| Admin platform (users / orgs / scans / monitors / analytics / settings) | `ADMIN_EMAILS`, `ADMIN_TOKEN` |
| Frontend scanner + full product dashboard | `VITE_API_URL` + SEO images |

No stubbed code paths remain — the experimental inference module was removed in Phase 10.
