# Phase 7 — Continuous AI Visibility Monitoring — Completion Report

AEOMirror is now a **continuous monitoring platform**, not just a one-time scanner.
Users create **monitors** that scan a website on a schedule (daily / weekly /
monthly / manual), keep a full **history**, **detect changes** vs the previous scan,
track **trends**, raise **alerts**, and send **email summaries** — all driven by a
background **scheduler/worker** that runs independently of HTTP requests and is
built for future distributed workers.

Billing and production deployment were intentionally **not** implemented.

## 1. Files created

**Backend — `app/monitoring/` (scheduling separated from scanning)**
- `scheduler.py` — the job queue: frequency math, `enqueue_due` (dedup: one active
  job per monitor), atomic claim-based `claim_job`, `complete_job`, retrying `fail_job`.
- `runner.py` — `run_scan_for_monitor` / `process_job`: runs a scan (via the shared
  scanning primitive), records history, detects changes, raises alerts, notifies,
  and updates the monitor.
- `changes.py` — change detection (overall + per-category deltas, status
  transitions, issue-count change, improvements/regressions).
- `alerts.py` — the alert engine (score drop, robots blocked, sitemap/schema
  removed, metadata/accessibility/performance regressions, critical issues).
- `notifications.py` — critical-alert emails + weekly/monthly summaries via Resend,
  recorded in `notification_log`.
- `worker.py` — the in-process async worker (`tick()` + loop), gated off in tests.
- `__init__.py`.

**Backend — API / schemas / migration / tests**
- `app/api/routes_monitors.py` — monitors CRUD, run, alerts, history/trends.
- `app/schemas/monitor.py`.
- `alembic/versions/a4f7c2e9b013_phase7_monitoring.py`.
- `tests/test_monitoring.py` — 15 tests.

**Frontend**
- `src/dashboard/Monitoring.jsx` — monitors list (active/paused), live polling,
  open-alerts strip, add-monitor form, empty/loading/error states.
- `src/dashboard/MonitorDetail.jsx` — timeline, charts, detected changes, alert
  history, scan history, latest-report link.

## 2. Files modified

- `backend/app/config.py` — scheduler + alert-threshold settings.
- `backend/app/db/models.py` — `Monitor`, `ScheduledJob`, `MonitorHistory`,
  `Alert`, `NotificationLog`.
- `backend/app/main.py` — mounts the monitors router; starts/stops the worker in
  the lifespan (only when `SCHEDULER_ENABLED` and not under tests).
- `backend/.env.example` — new settings.
- `frontend/src/api.js` — monitoring API layer.
- `frontend/src/dashboard/Dashboard.jsx` — "Monitoring" nav + detail view wiring.
- `frontend/src/dashboard/dashboard.css` — monitoring styles.

## 3. Database migrations

`a4f7c2e9b013` (revises `9c2e6b41d7a8`) — additive, reversible. Creates
`monitors`, `scheduled_jobs`, `monitor_history`, `alerts`, `notification_log` with
indexes for due-monitor lookup (`status,next_scan_at`), claimable jobs
(`status,run_after`), and history/alert retrieval.

## 4. API endpoints

All authenticated + org-scoped (RBAC: view = `report:view`, create/run/update/ack =
`scan:run`, delete = `scan:delete`):
`POST /monitors` · `GET /monitors` · `GET /monitors/{id}` · `PATCH /monitors/{id}` ·
`DELETE /monitors/{id}` · `POST /monitors/{id}/run` · `GET /alerts` ·
`POST /alerts/{id}/acknowledge` · `GET /history/{monitor_id}`.

## 5. Monitoring architecture

A **monitor** owns a URL + frequency + status and a rolling snapshot
(`last/next_scan`, `latest_score`, `latest_scan_id`). Each completed scan appends a
**monitor_history** row storing a compact snapshot (per-signal scores, issue count,
and the detected **changes** diff) so trend/history retrieval never re-parses scans.
A manual run executes inline (immediate result); scheduled runs go through the
worker. Reports and scans from a monitor reuse the Phase 3–6 pipeline unchanged.

## 6. Scheduler architecture

Deliberately **separate from scanning**. `scheduled_jobs` is a **claim-based queue**:
`enqueue_due` creates one job per due monitor (deduped, advancing `next_scan_at`);
the worker atomically flips `pending → running` (status-guarded UPDATE, safe for
many workers), runs it, then `completed` or — on failure — retries with backoff up
to `max_attempts`, else `failed`. The in-process worker's `tick()` is a pure
function of the DB, so the **exact same logic runs in a separate/for-scale worker
process**: set `SCHEDULER_ENABLED=false` and run a loop calling `tick()`. Nothing
else changes. Manual runs and the drop of the scan cache on `run_scan` mean
recurring scans are always fresh, and dedup guarantees **no duplicate scans**.

## 7. Alert engine design

Rule-based comparison of the new scan vs the previous one. Change-based alerts fire
only when a baseline exists; "critical issue" alerts also fire on the first scan.
Types: **score_drop** (warning/critical by magnitude), **robots_blocked** (an AI
crawler newly disallowed or a blanket block), **sitemap_removed**, **schema_removed**,
**metadata_problem**, **accessibility_regression**, **performance_degradation**, and
**critical_issue** (a signal newly at/below the critical score). Thresholds are
configurable. Critical alerts trigger an immediate email; all alerts are stored and
acknowledgeable, and feed the monitor's alert count + critical-alerts timeline.

## 8. Remaining work for Phase 8

- Extract the worker to a **standalone process / container** (the `tick()` seam and
  claim-based queue already support it) and add distributed locking on Postgres
  (`SELECT … FOR UPDATE SKIP LOCKED`).
- **Upcoming-scan** notifications (function seam noted as future-ready).
- Per-user alert routing + notification preferences (currently owner/creator).
- Anomaly detection / smarter thresholds; alert digest batching.
- Longer-horizon history retention + downsampling for large datasets.
- Deferred: billing/subscriptions, production deployment.

## Verification

- Backend: **123 passed, 1 skipped** (`pytest`). `test_monitoring.py` covers monitor
  CRUD + org scoping, scheduler enqueue/**dedupe**, worker `tick()` running a due
  monitor, recurring scans + **history tracking**, **change detection & alert
  generation** (robots blocked / schema & sitemap removed / score drop), alert
  acknowledgement, **trend calculations**, **email notifications** (critical +
  weekly summary + baseline logic), and **failed-job retries**.
- Live: with the worker enabled (5s tick), a newly created daily monitor was
  auto-scanned within ~4s (score 63) **with no HTTP trigger**. The monitoring UI
  (empty state → create → Run now → detail with score/category/issue charts + scan
  history) was driven end-to-end with zero console errors.
- Migration applies and downgrades cleanly; dev DB at head `a4f7c2e9b013`.
- No LLM/paid API in any scanner path; no secrets logged or committed.
