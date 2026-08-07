# SECURITY — AEOMirror

How AEOMirror protects data and accounts, and the controls verified for the
production launch.

## Authentication & sessions
- **Passwords**: bcrypt (cost 12); strength policy enforced server-side; never logged.
- **Access tokens**: short-lived JWT (HS256, 15 min), kept in memory on the client.
- **Refresh tokens**: opaque, high-entropy, stored **only as a SHA-256 hash**;
  delivered in an `HttpOnly` + `SameSite=Strict` + `Secure` (production) cookie;
  **rotated on every refresh** with reuse invalidation; revoked on logout / password
  reset / "log out everywhere".
- **Login** is rate-limited per email+IP; login and forgot-password responses never
  reveal whether an account exists.
- **JWT_SECRET** is required in production (startup fails without it).

## Authorization
- **RBAC** (Owner/Admin/Member/Viewer) enforced by FastAPI dependencies on every
  product endpoint; a resource outside your org returns **404**, not 403.
- **Platform admin** (`/admin/*`) is a separate flag/allowlist; the router is gated by
  `get_admin`.
- **Billing entitlements** gate premium features server-side (never client-trusted).

## Input & data
- **Validation**: Pydantic v2 models validate every request body/query.
- **SQL injection**: SQLAlchemy ORM with bound parameters throughout — no string SQL
  with user input.
- **XSS**: the API returns JSON only; the React frontend escapes by default; all
  user-controlled values in generated emails/PDFs are HTML-escaped.
- **SSRF**: the scanner validates URLs and blocks private/loopback/link-local/reserved
  ranges, re-checking on every redirect; response size is capped and streamed.

## Transport & headers
- **HTTPS** everywhere; **HSTS** (`ENABLE_HSTS=true`) with preload on the frontend.
- API security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, `Permissions-Policy`, and a strict **CSP** (`default-src 'none'`
  for JSON responses; relaxed only for `/docs`).
- Frontend **CSP**, HSTS, and framing protection set via `vercel.json`.
- **CORS**: explicit allow-list of origins (never `*`), credentials enabled for the
  refresh cookie; `Content-Disposition` exposed for downloads.

## CSRF
- Mutations are **Bearer-authenticated** (not cookie-authenticated), so they are not
  CSRF-able. The only cookie (refresh) is `SameSite=Strict` and used solely by
  `/auth/refresh` + `/auth/logout`.

## Payments
- Stripe **webhook signatures are verified** (HMAC scheme) before any state change.
- **Idempotency**: every provider event is recorded in `payment_events`
  (`provider_event_id` unique); duplicates are ignored — no double-charge.
- Frontend-reported payment status is **never trusted**; only verified webhooks (or the
  dev-only completion path, disabled in production) change state.

## Secrets
- No secrets in Git: `.env`, `.env.local`, `.env.*.local` are gitignored;
  `.env.example` documents every variable with placeholders only.
- Production secrets live in Render/Vercel dashboards. Secrets are never logged
  (only exception types are logged for provider/Redis errors).

## Rate limiting & abuse
- Scan endpoint: fixed-window per-IP limiter (Redis-backed, fail-open) blunts bursts
  — a defense-in-depth ceiling, not a billing limit.
- Login: per email+IP limiter.
- Scanning is account-based: the Free plan gets 1 scan job/month + a one-time 50-URL
  bulk trial (scores only); Pro gets 15 scan jobs/month. Premium features (full bulk
  per-page detail, AI-written reports, AI Content Insights, exports) are gated by plan.

## Observability & hardening for launch
- Interactive docs (`/docs`, `/redoc`) are **disabled in production**.
- `/metrics` and `/debug/*` require `ADMIN_TOKEN` in production.
- Structured JSON request logs with request IDs; stack traces never returned to
  clients. Optional Sentry error tracking (`SENTRY_DSN`).
- All placeholder/stub endpoints and the experimental inference module were removed.

## Reporting
Email security reports to `security@aeomirror.com`. Please allow coordinated
disclosure before public posting.
