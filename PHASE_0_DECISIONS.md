# Phase 0 — Architecture Decisions

Status: **for review**. These answers are scoped to what is technically verifiable
from *this* repository as of 2026-07-23. Where a business input is required, it is
flagged for Ayush rather than guessed.

---

## 1. Reuse The Doc Mirror's fetch / queue / cache spine, or keep AEOMirror fresh?

**Answer: cannot be technically verified from this repository alone — recommend
keeping the current fresh implementation for now.**

- **The Doc Mirror source is not present in this repo.** There is no shared
  package, submodule, workspace reference, or import of any Doc Mirror module
  anywhere in `backend/` or `frontend/`. The only mentions of "The Doc Mirror"
  are prose in `README.md` (suite positioning). There are **no integration hooks**
  pointing at a shared spine — `INTEGRATIONS.md` lists only external providers
  (Postgres, Redis, ESP, LLM APIs), never an internal Nextdot service.
- Therefore any claim that the Doc Mirror spine "can be reused as-is" is
  **unverifiable here** and should not drive Phase 1.
- What AEOMirror already has today (all first-party, no external spine):
  - `backend/app/core/fetch.py` — size/timeout/redirect-capped HTTP fetch
  - `backend/app/core/ssrf.py` — SSRF validation (private/loopback rejection)
  - `backend/app/core/cache.py` — `TTLCache` (24h dedupe) + `RateLimiter`, both
    with deliberately tiny interfaces so a Redis swap is mechanical
  - There is **no queue** in AEOMirror today; scans run synchronously in-request.

**Recommendation.** Treat reuse as an open question owned by whoever has access to
the Doc Mirror codebase. Concretely:
  1. Ship Phase 1 on AEOMirror's own spine (it is already interface-clean).
  2. Separately, obtain the Doc Mirror repo and do a real interface comparison
     (fetch signature, cache contract, queue semantics) before committing to reuse.
  3. Only adopt a shared spine if it is offered as a versioned package with a
     stable contract — do not copy-paste source across products.

*Open input needed:* access to the Doc Mirror repository to make this a verified
decision instead of a deferred one.

---

## 2. What peak scan rate should v1 handle?

**Answer: no confirmed business number exists in this repo — below is an
engineering baseline and a recommended initial target. Ayush must confirm the
real business figure.**

Assumptions (all derived from code / defaults in this repo):
- The free scan is **I/O-bound, not CPU-bound**: one scan = a handful of capped
  HTTP fetches (HTML + robots.txt + llms.txt + sitemap) then deterministic
  scoring. No LLM/paid calls (enforced rule).
- `fetch_timeout_seconds = 12`, response capped at 3 MB (`app/config.py`).
- 24h cache dedupe by normalized URL means repeat/popular URLs are ~free.
- Current per-IP limit default: `free_scans_per_window = 3` per `3600s`
  (`app/config.py`) — a per-abuser guard, **not** a system throughput number.
- Current cache + rate limiter are **in-memory / per-process** — real horizontal
  throughput is gated on the Redis swap (Phase 1, A3) and on Postgres (A1).

Recommended initial engineering targets for v1 (to design and load-test against,
pending business confirmation):
- **Baseline capacity target:** ~**20–50 scans/sec** sustained per app instance,
  bursting higher on cache hits. Rationale: I/O-bound work with a 12s worst-case
  fetch means concurrency (async workers), not CPU, is the limit; a single
  Uvicorn instance with async fetch handles tens of concurrent in-flight scans
  comfortably, and Redis-backed caching absorbs popular-URL bursts.
- **Rate-limit target (anonymous free tier):** keep a small per-IP window
  (order of the current 3/hour, tune with product) plus a **global backpressure
  limit** so one campaign cannot saturate outbound fetch capacity.
- **Load-test target:** script a mixed workload (e.g. 80% cache-hit / 20% cold)
  at **2–3× the expected peak**, assert p95 latency and zero 5xx, and confirm the
  Redis-backed rate limiter holds across >1 worker before going live.

*Open input needed:* Ayush to confirm the real expected peak (launch traffic,
campaign spikes, agency batch scans). The numbers above are a safe starting
envelope, not a committed SLA.

---

## 3. Confirm the stack: keep Python/FastAPI, or port to Node?

**Answer: keep the shipped Python / FastAPI stack. Do not port.**

Evidence from this repo:
- The entire free scanner — the product's core and its one hard correctness rule
  (no LLM/paid calls) — is **already built, and tested with 11 passing tests** on
  FastAPI: `scanner/`, `core/fetch.py`, `core/ssrf.py`, `core/cache.py`,
  `api/routes_scan.py`, `db/`.
- The API contract, SSRF guard, rate limiting, caching, persistence, and lead
  capture are all implemented and green. A port would re-risk all of it for no
  product gain.
- `INTEGRATIONS.md` explicitly scopes remaining work as *drop-in* on this stack
  (Postgres URL, Redis, ESP, LLM adapters) — none of it argues for Node.
- No compelling blocker is present in the repository: no performance wall (work
  is I/O-bound and already async via `httpx`), no missing-library problem, no
  contract that Node would satisfy and Python would not.

**Recommendation.** Stay on Python/FastAPI. Revisit only if a *specific, verified*
blocker appears (e.g. a required capability with no viable Python path), and only
with explicit approval — a rewrite is not something to trigger implicitly.

*The frontend is already Node (Vite + React); this decision is only about the
backend/API service.*
