# RUNBOOK — AEOMirror

Incident playbooks and the launch smoke test. Keep this short and actionable.

## Severity
- **SEV1** — site/API down, data loss, payment or auth broken for everyone.
- **SEV2** — a major feature broken (monitoring, reports, billing) but core works.
- **SEV3** — degraded/partial, workaround exists.

## First 5 minutes (any incident)
1. Check `GET /healthz` and `GET /readyz` on `api.aeomirror.com`.
2. Open `/admin/system` (platform admin) — DB, Redis, queue, scheduler, email, memory,
   disk at a glance.
3. Check Render + Vercel dashboards (deploys, logs, restarts) and Sentry (if enabled).
4. Declare severity; start a timeline.

## Playbooks

### API 5xx / down (SEV1)
- `/readyz` 503 → **database** issue: check Render Postgres status/connections; check a
  bad migration on the latest deploy → **roll back the deploy** in Render.
- `/readyz` ok but 5xx → check API logs for the failing endpoint + `request_id`;
  roll back the last deploy if it correlates.
- Mitigation: enable **maintenance mode** via `/admin/settings` to shed scan load.

### Database unreachable (SEV1)
- Verify `DATABASE_URL`; check pool exhaustion (raise `DB_POOL_SIZE` or DB tier).
- If corrupted/lost → follow **BACKUP.md → Restore procedure**.

### Redis down (SEV3)
- Cache + rate limiter **fail open** — scanning keeps working. Fix Redis; no data loss.
  Rate limits/cache are per-process until Redis returns.

### Scheduled scans not running (SEV2)
- Confirm the `aeomirror-worker` service is up (Render). It must have
  `SCHEDULER_ENABLED=true`; the web service must have it `false`.
- Inspect `scheduled_jobs`: many `failed` → check the error column + worker logs.
  Failed jobs auto-retry with backoff up to `MONITOR_JOB_MAX_ATTEMPTS`.

### Billing / webhooks failing (SEV2)
- Stripe dashboard → webhook deliveries. 400 = signature mismatch → verify
  `STRIPE_WEBHOOK_SECRET`. 5xx = processing error → check API logs; Stripe retries.
- Duplicate/replayed events are ignored (idempotent) — safe to let Stripe retry.
- Never toggle subscription state by hand except as a documented manual fix.

### Emails not sending (SEV3)
- Check `RESEND_API_KEY`/`EMAIL_FROM` and the `email` feature flag; inspect
  `notification_log` (status `skipped`/`failed`). User actions are unaffected.

### Suspected account/security incident (SEV1)
- Rotate `JWT_SECRET` (logs users out), revoke sessions, force password resets for
  affected users. Review `/admin/logs` (audit trail). See SECURITY.md.

## Launch smoke test (run after every production deploy)
```
[ ] GET /healthz = 200, GET /readyz = 200
[ ] GET /billing/plans returns free/report/pro
[ ] Register a new account → lands on dashboard (verification email logged/sent)
[ ] Run a scan from the homepage → score + report render
[ ] Open a report → on-screen renders; PDF download gated for Free (402) then works on Pro
[ ] Upgrade to Pro (Stripe test mode) → webhook activates subscription + invoice + receipt
[ ] Create a monitor (Pro) → worker runs it; history + trend appear
[ ] Trigger/verify an alert; check email logged
[ ] Cancel then resume the subscription
[ ] Admin: /admin dashboard, users, analytics, system, audit logs load; a normal user is bounced
[ ] Mobile: marketing + dashboard usable at 375px width
[ ] SEO: view-source shows title/description/OG/Twitter/JSON-LD; /robots.txt + /sitemap.xml served
```

## Rollback
- **Frontend**: Vercel → Deployments → promote the previous good deployment.
- **Backend/worker**: Render → Deploys → roll back to the previous image. If a
  migration must be reverted, `alembic downgrade -1` (prefer roll-forward).

## Contacts
- On-call: `ops@aeomirror.com` · Security: `security@aeomirror.com`
- Providers: Render, Vercel, Stripe, Resend, Sentry dashboards.
