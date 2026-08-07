# BACKUP & DISASTER RECOVERY — AEOMirror

The database is the only stateful system of record. Redis is a cache/queue and is
reconstructable; the frontend and API are stateless (rebuilt from Git).

## What to back up
- **PostgreSQL** (`aeomirror-db`) — users, orgs, scans, reports, monitors, alerts,
  subscriptions, payments, invoices, audit logs. **This is the critical asset.**
- **Secrets** — stored in Render/Vercel; keep a copy in your password manager.
- **Redis** — not backed up (fail-open cache + rebuildable job queue).

## Backup strategy (PostgreSQL)
- **Managed automatic backups**: enable Render PostgreSQL automatic daily backups
  with **7–30 day** retention (point-in-time recovery on paid tiers).
- **Off-platform logical dump** (defense in depth), daily via cron/CI:
  ```bash
  pg_dump "$DATABASE_URL" --no-owner --format=custom \
    --file "aeomirror-$(date +%F).dump"
  # upload to object storage (e.g. S3/R2) with lifecycle expiry + versioning
  ```
- Retain: 7 daily, 4 weekly, 12 monthly. Encrypt at rest; restrict access.
- **Test restores monthly** (a backup you haven't restored is not a backup).

## Restore procedure
1. Provision a fresh PostgreSQL instance (or a scratch DB for a test restore).
2. Restore the dump:
   ```bash
   pg_restore --no-owner --clean --if-exists \
     --dbname "$TARGET_DATABASE_URL" aeomirror-YYYY-MM-DD.dump
   ```
3. Confirm schema is at head: `alembic current` should show the latest revision;
   run `alembic upgrade head` if needed.
4. Point the API/worker `DATABASE_URL` at the restored instance and redeploy.
5. Run the smoke checklist in `RUNBOOK.md`.

## Disaster recovery checklist
- [ ] Declare the incident; note start time and impact (see `RUNBOOK.md`).
- [ ] Identify the last known-good backup (managed snapshot or logical dump).
- [ ] Provision replacement Postgres; **restore** using the procedure above.
- [ ] Restore/verify secrets in Render + Vercel.
- [ ] Redeploy backend + worker (Render) and frontend (Vercel) from `main`.
- [ ] Verify `/healthz` + `/readyz`, then auth, scan, report, billing, monitoring.
- [ ] Re-point DNS (`aeomirror.com`, `api.aeomirror.com`) if the host changed.
- [ ] Rotate any potentially exposed secrets (`JWT_SECRET` rotation logs everyone out).
- [ ] Reconfigure the Stripe webhook endpoint + signing secret if the API URL changed.
- [ ] Post-incident review; record RTO/RPO actuals.

## Targets
- **RPO** (max data loss): ≤ 24h with daily backups; near-zero with PITR.
- **RTO** (time to recover): ≤ 1h for a managed-snapshot restore + redeploy.

## Notes
- **Never** restore a production dump onto a machine with `ALLOW_PRIVATE_HOSTS=true`.
- Migrations are forward-only and reversible (`alembic downgrade` exists per revision)
  but prefer roll-forward in production.
