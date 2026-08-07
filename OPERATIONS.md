# OPERATIONS — AEOMirror

Day-2 operations: monitoring, scaling, migrations, config, and routine tasks.

## Services
- `aeomirror-api` (Render web, Docker) — FastAPI, 2 uvicorn workers, `SCHEDULER_ENABLED=false`.
- `aeomirror-worker` (Render worker, Docker) — `python run_worker.py`, drains the
  scheduled-scan queue (`SCHEDULER_ENABLED=true`).
- `aeomirror-db` (PostgreSQL 16), `aeomirror-redis` (Redis).
- Frontend on Vercel (static).

## Health & metrics
| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /healthz` (`/health`) | none | Liveness (used by Render health check) |
| `GET /readyz` (`/ready`) | none | Readiness — 503 if DB unreachable |
| `GET /metrics` | `X-Admin-Token` in prod | Prometheus counters/gauges |
| `GET /debug/metrics` | `X-Admin-Token` in prod | JSON metrics + durable scan counts |
| `GET /admin/system` | platform admin | DB/Redis/queue/scheduler/email/memory/disk |

Point Prometheus (or Render metrics) at `/metrics`. Logs are single-line JSON with
`request_id`, method, path, status, latency — ship to your log store and alert on
error-rate and p95 latency.

## Scaling
- **API**: raise `WEB_CONCURRENCY` and/or the Render instance size/count. Stateless —
  scale horizontally freely.
- **Worker**: the queue is **claim-based** (status-guarded `pending→running` update),
  so you can run **multiple** worker instances safely; only one wins each job.
- **PostgreSQL**: connection pool is bounded (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`,
  `pool_pre_ping`). Scale the DB tier before adding many API instances.
- **Redis**: shared cache + rate-limit + is fail-open; sizing is modest.

## Migrations
Schema is owned by Alembic (never `create_all` at startup). The API container runs
`alembic upgrade head` on boot (`entrypoint.sh`).
```bash
alembic current            # show applied revision
alembic history            # list revisions
alembic upgrade head       # apply (also runs on deploy)
alembic downgrade -1       # roll back one (prefer roll-forward in prod)
npm run db:upgrade         # convenience wrapper (from repo root)
```
Deploy order for breaking changes: ship additive migration → deploy code → later
migration to drop old columns. All Phase 0–10 migrations are additive + reversible.

## Feature flags & settings (admin)
`/admin/settings` (platform admin) toggles feature flags (`monitoring`, `pdf_export`,
`email`, `alerts`, `experimental`) and system settings (**maintenance mode**, global
scanner version, default rubric, email-from name). Maintenance mode blocks scans +
monitor creation for non-admins (returns 503).

## Common tasks
- **Grant a platform admin**: add their email to `ADMIN_EMAILS`, or set
  `users.is_platform_admin=true`.
- **Refund / dispute**: handled automatically from Stripe webhooks
  (`charge.refunded` / `charge.dispute.created`) → payment marked, subscription
  cancelled. Verify in `/admin` (payments) and `GET /billing/payments`.
- **Resend a receipt/invoice**: re-trigger from Stripe, or inspect `notification_log`.
- **Rotate JWT secret**: change `JWT_SECRET` → all users must re-log in (refresh
  cookies still valid until they hit an access-token check; force logout by also
  revoking sessions if needed).

## Email
Transactional email via Resend (`RESEND_API_KEY` + `EMAIL_FROM`). Without them, sends
are **skipped but logged** in `notification_log` (status `skipped`) — the app never
fails a user action because email is down. Gate globally with the `email` feature flag.

## Alerting (recommended)
- API error rate > 2% (5m) or p95 latency > 1s.
- `/readyz` failing (DB down).
- Queue backlog: `scheduled_jobs` pending > 100 or failed ≥ 5 (`/admin/system`).
- Redis unreachable (cache/limiter degraded — fail-open, informational).
- Stripe webhook 4xx/5xx spikes.

See `RUNBOOK.md` for incident playbooks and `BACKUP.md` for recovery.
