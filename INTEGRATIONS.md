# INTEGRATIONS.md

This file lists exactly what your developer must wire up. Everything not listed
here is already built and working. Items are ordered by when you need them:
the first three make the free scanner production-grade; the rest unlock paid
features and can wait until the free scanner is live and pulling leads.

Each item marks the file(s) to touch. Search the codebase for `TODO` and
`INTEGRATION POINT` to find every hook in place.

---

## A. Required to take the FREE scanner to production

### A1. Postgres (replace SQLite)
- **File:** `backend/.env` → `DATABASE_URL`
- **Do:** set `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/aeomirror`,
  uncomment `psycopg2-binary` in `requirements.txt`, install.
- **Note:** the ORM models are already Postgres-compatible. No code change.

### A2. Alembic migrations (replace create_all)
- **File:** `backend/app/db/session.py` (`init_db`) and app startup
- **Do:** initialize Alembic, generate the first migration from the existing
  models, and remove the `init_db()` call in `app/main.py` startup. `create_all`
  is a dev convenience only.

### A3. Redis (replace in-memory cache + rate limiter)
- **Files:** `backend/app/core/cache.py`, `backend/.env` → `REDIS_URL`
- **Why:** the in-memory `TTLCache` and `RateLimiter` are per-process. With more
  than one worker, cache dedupe and rate limits must be shared.
- **Do:** reimplement `TTLCache.get/set` and `RateLimiter.check` against Redis.
  The interfaces are tiny and deliberately unchanged at every call site, so this
  is a mechanical swap.

---

## B. Lead capture email

### B1. Email provider (welcome / report email)
- **File:** `backend/app/services/leads.py` → `send_welcome_email()`
- **Do:** replace the print stub with a real ESP call (Resend, AWS SES,
  Postmark). Add the key to `.env` (e.g. `RESEND_API_KEY`). The lead is already
  captured and stored in the `leads` table; only the send is stubbed.

---

## C. Step 5: Auth and billing (paid product)

### C1. Authentication
- **File:** `backend/app/api/routes_misc.py` → `/v1/auth/signup`, `/v1/auth/login`
  (currently return 501)
- **Do:** implement signup/login. Recommended: a hosted provider (Clerk, Auth0,
  Supabase Auth) or FastAPI JWT. Add `users` and `organizations` tables
  (schema is in the PRD). Protect the paid routes with an auth dependency.

### C2. Domain verification gate
- **Do:** before any paid action on a domain (scans that cost money, fix
  generation, monitoring), require the user to verify domain ownership
  (DNS TXT record or a hosted file). The free structural scan does not need
  this; anything metered does.

### C3. Billing
- **Do:** integrate Stripe. Create the plan tiers (Scan $29 / Track $69 /
  Compound $99 / Agency $299), map them to the `plans` table, and enforce
  plan limits (sites, prompts, engines) before running metered work.
  Add `STRIPE_SECRET_KEY` to `.env`.

---

## D. Step 6: Paid inference (the metered engine)

All interfaces exist in `backend/app/services/inference/`. Replace the stubs in
`stub.py` with real providers. `base.py` defines the contracts; do not change
them, so the rest of the app is unaffected.

### D1. Prompt simulation (answer visibility)
- **File:** `backend/app/services/inference/stub.py` → `StubPromptSimulator.run`
- **Do:** call each engine's API (OpenAI, Anthropic, Google, Perplexity) with the
  prompt, run each several times, and report brand mention / citation / position
  **with a confidence interval**. Add the keys to `.env`.
- **Honesty requirement (important):** results measured via a provider API must
  be labelled as API-measured, not consumer-surface (ChatGPT app / AI Overviews)
  measured. The two differ. Do not present a heuristic as a real citation rate.

### D2. Fix generation
- **File:** `backend/app/services/inference/stub.py` → `StubFixGenerator.generate`
- **Do:** call an LLM with a schema-constrained prompt that reads the page HTML
  and emits a valid, copy-paste-ready asset (Organization JSON-LD, FAQPage
  schema, llms.txt). Validate the output parses before returning it.

### D3. Enforce budget
- **Do:** every call in D1/D2 must decrement the caller's plan quota before
  executing. This is what keeps token COGS below the price of each tier.

---

## E. Frontend

### E1. Point at the deployed API
- **File:** `frontend/.env` → `VITE_API_URL`
- **Do:** set it to your deployed API URL and rebuild. The scanner already calls
  the real API and falls back to mock only when the API is unreachable.

### E2. Dashboard data (currently mock)
- **File:** `frontend/src/App.jsx` (the dashboard screens)
- **Do:** the single-site Overview and the secondary screens (Scans, Answer
  visibility, Competitors, Fixes, Reports) render mock data. Wire them to the
  real endpoints as those endpoints come online (scan history exists now;
  monitoring and fixes come with Step 6).

---

## Quick reference: what is real vs stubbed today

| Real and working | Stubbed (you implement) |
|---|---|
| 6-family scanner + ARS scoring | Auth (signup/login → 501) |
| POST /v1/scan (fetch, score, persist) | Billing (Stripe) |
| SSRF guard, rate limit, cache | Email send (capture is real) |
| Lead capture + storage | Prompt simulation (interface + stub) |
| Scan retrieval by ID | Fix generation (interface + stub) |
| Frontend scanner + dashboard shell | Dashboard live data |
