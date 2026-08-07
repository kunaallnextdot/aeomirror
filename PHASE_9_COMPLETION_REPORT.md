# Phase 9 — Billing & Subscriptions — Completion Report

AEOMirror now has a complete, modular billing system: a **Free** plan, a **one-time
Report** purchase, and a **Pro** monthly subscription, with secure checkout,
signature-verified webhooks, subscription management, invoices/receipts, and
plan-based **feature gating**. Payment providers are abstracted so Razorpay/Paddle
can be added without touching the billing logic. Production deployment was
intentionally **not** started.

## 1. Files created

**Backend — `app/billing/`**
- `plans.py` — plan definitions (seed + `/plans`) and the entitlements matrix.
- `entitlements.py` — resolve an org's effective plan, limits, and per-scan unlocks
  (all-access when enforcement is off).
- `providers/base.py` — `PaymentProvider` interface + `CheckoutResult`/`ProviderEvent`.
- `providers/stripe_provider.py` — Stripe checkout + **webhook signature verification**
  (real Stripe HMAC scheme, testable offline) + dev-mode checkout.
- `providers/__init__.py` — provider registry (`get_provider`).
- `service.py` — checkout creation, the single **idempotent** `process_event`
  (all webhook types), invoice generation, cancel/resume, dev completion.
- `emails.py` — receipt, invoice, activated, cancelled, renewal-reminder, payment-failed.
- `__init__.py`.

**Backend — API / schemas / migration / tests**
- `app/api/routes_billing.py`; `app/schemas/billing.py`.
- `alembic/versions/c9e5a71f4b28_phase9_billing.py` (seeds plans).
- `tests/test_billing.py` — 15 tests.

**Frontend**
- `src/dashboard/BillingView.jsx` — plan/usage/upgrade/invoices/payments + cancel/resume.

## 2. Files modified

- `backend/app/config.py` — billing settings (enforcement, Stripe keys, prices, limits).
- `backend/app/db/models.py` — `Plan`, `Subscription`, `Payment`, `PaymentEvent`, `Invoice`.
- `backend/app/main.py` — mounts the billing router.
- Feature gating wired into `routes_scan.py` (Free monthly quota + a cache-hit
  ownership fix), `routes_monitors.py` (Pro-only monitoring), `routes_reports.py`
  (export downloads gated), `routes_org.py` (Pro-only team), `routes_dashboard.py`
  (Free history cap).
- `backend/tests/conftest.py` — billing enforcement off in tests + plan seeding.
- `backend/.env.example`.
- `frontend/src/api.js` — billing API + `startCheckout`.
- `frontend/src/dashboard/Dashboard.jsx` — "Billing" nav + view.
- `frontend/src/dashboard/ReportView.jsx` — one-time "Unlock this report" paywall.
- `frontend/src/dashboard/dashboard.css` — billing styles.

## 3. Database migrations

`c9e5a71f4b28` (revises `b8d3f1a20c47`) — additive, reversible. Creates `plans`,
`subscriptions`, `payments`, `payment_events`, `invoices` and **seeds** the three
plans. `payment_events.provider_event_id` is unique (webhook idempotency).

## 4. API endpoints

`GET /billing/plans` (public) · `GET /billing/subscription` · `POST /billing/checkout`
· `POST /billing/checkout/complete` (dev) · `POST /billing/webhooks/{provider}` ·
`PATCH /billing/subscription/cancel` · `PATCH /billing/subscription/resume` ·
`GET /billing/payments` · `GET /billing/invoices`.

## 5. Billing architecture

Checkout creates a **pending** `payment` with an opaque reference, then asks the
configured provider for a hosted checkout URL (real Stripe Session when keyed;
otherwise a dev-completion URL). State changes happen **only** from verified
provider webhooks (or the dev-completion path), never from the frontend. Every
webhook is verified, recorded in `payment_events`, and funneled through one
**idempotent** `process_event` that dispatches by normalized type
(payment success/failed, subscription created/renewed/cancelled, refund,
chargeback). Providers are pluggable via a registry — adding Razorpay/Paddle means
implementing `PaymentProvider` only.

## 6. Subscription architecture

Subscriptions are **org-level**. An org's plan = its active subscription's plan,
else Free. Upgrade = Pro checkout → `subscription` active + `invoice` + receipt/
activation emails. Cancel sets `cancel_at_period_end` (stays Pro until period end);
Resume clears it; refund/chargeback cancel immediately. Renewals extend the period
and generate a new invoice + receipt. A **one-time Report** purchase records a
succeeded `payment` with a `scan_id` and unlocks only that scan's exports — it does
**not** change the org's plan.

## 7. Feature gating summary

Enforced when `BILLING_ENFORCED=true` (per-org, in addition to the Phase 8 global
flags):
- **Monitoring** → Pro only (402 otherwise).
- **Report exports** (PDF/CSV/JSON) → Pro, or a one-time purchase for that scan;
  on-screen report stays free.
- **Scans** → Free capped to `FREE_MONTHLY_SCANS`/month; Pro unlimited (anonymous
  scans keep the IP rate limit).
- **Team invites** → Pro only.
- **History** → Free sees its most recent `FREE_HISTORY_LIMIT` scans; Pro unlimited.

Security: webhook signatures verified before any state change; duplicate events
ignored via `payment_events`; frontend payment status never trusted; the dev
completion endpoint is disabled in production and when a real Stripe key is set.

## 8. Remaining work for Phase 10

- Real Stripe hosted-checkout + customer portal wiring end-to-end (keys/prices),
  and the Razorpay/Paddle providers behind the same interface.
- Proration on upgrade/downgrade, coupons/trials, tax handling, and dunning
  (retry schedule) for failed renewals.
- Downloadable PDF invoices and a billing-history export.
- Usage-based add-ons / seat-based team pricing.
- Deferred: production deployment.

## Verification

- Backend: **153 passed, 1 skipped** (`pytest`). `test_billing.py` covers plans,
  checkout + dev completion, **webhook signature verification** (valid/invalid/
  idempotent), the subscription lifecycle (activate/cancel/resume), the **refund**
  flow, invoice + payment recording, the one-time report unlock (only that scan),
  and feature gating (monitoring/team/scan-quota/history) with enforcement flipped
  on. Also fixed a pre-existing cache-hit bug where an authenticated scan returned
  another org's scan id.
- Live: a Free org saw the usage meter + paywall, unlocked one report ($9) and
  downloaded its PDF, upgraded to Pro ($29 → unlimited), saw invoices
  (`AEO-2026-0000x`) + succeeded payments, and cancelled→resumed — zero console
  errors. Full lifecycle + gating also verified via curl.
- Migration applies and downgrades cleanly; dev DB at head `c9e5a71f4b28`.
- No LLM/paid API in any scanner path; no secrets logged or committed.
