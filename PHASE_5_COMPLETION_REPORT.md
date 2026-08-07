# Phase 5 — Authentication & Multi-User SaaS — Completion Report

AEOMirror is now a secure multi-user application. Every user owns an
**organization** with a **private dashboard and scan history**, protected by JWT
auth, refresh-token sessions, and role-based access control. Billing, subscriptions,
monitoring, scheduled scans, and production deployment were intentionally **not**
started (out of scope for Phase 5).

## 1. Files created

**Backend**
- `app/core/security.py` — bcrypt password hashing, JWT access tokens (HS256),
  opaque refresh/verify/reset/invite token generation + sha256 hashing, password
  strength policy, slugify.
- `app/api/deps.py` — `get_current_user`, `get_optional_user`, `get_context`,
  the RBAC permission matrix, and `require_permission` / `require_role` factories.
- `app/api/routes_auth.py` — `/auth/*`, `/me`, `/me/sessions` endpoints.
- `app/api/routes_org.py` — `/org/*` (organization, members, invitations, accept).
- `app/schemas/auth.py` — request/response models.
- `app/services/auth_email.py` — verification / reset / invitation emails (Resend,
  best-effort; dev logs the link).
- `alembic/versions/7a1c9e4b2f60_phase5_auth_accounts.py` — migration.
- `tests/authutil.py`, `tests/test_auth.py`, `tests/test_rbac.py`.

**Frontend**
- `src/auth/client.js` — in-memory access token + `authFetch` (auto-refresh on 401).
- `src/auth/api.js` — auth + org API layer.
- `src/auth/AuthContext.jsx` — provider with silent session restore + permission mirror.
- `src/auth/router.jsx` — tiny path router + `navigate`.
- `src/auth/ui.jsx`, `src/auth/auth.css` — shared form primitives + styles.
- `src/auth/pages/`: `Login`, `Register`, `ForgotPassword`, `ResetPassword`,
  `VerifyEmail`, `AcceptInvitation`, `ProfilePage`, `OrganizationPage`, `TeamPage`.

## 2. Files modified

- `backend/app/config.py` — JWT/cookie/login-limit settings; production requires
  `JWT_SECRET`; cookies forced Secure in production.
- `backend/app/db/models.py` — `User`, `Organization`, `OrganizationMember`,
  `Session`, `PasswordReset`, `EmailVerification`, `Invitation`; `scans` gains
  `organization_id` + `user_id` (+ composite index).
- `backend/app/core/cache.py` — parametrized `RateLimiter` prefix; added `login_limiter`.
- `backend/app/api/routes_scan.py` — optional-auth attribution of scans to an org;
  `/v1/scan/{id}` no longer exposes org-owned scans.
- `backend/app/api/routes_dashboard.py` — now **auth-required + org-scoped**; actions
  gated by `report:view` / `scan:run` / `scan:delete`.
- `backend/app/main.py` — mounts auth/org routers; CORS allows PATCH/DELETE + Authorization.
- `backend/.env.example`, `backend/requirements.txt` (PyJWT, bcrypt, email-validator).
- `frontend/src/main.jsx` — wraps app in `AuthProvider`.
- `frontend/src/App.jsx` — path router + route guard + auth-aware top bar (replaces
  the dev view switcher).
- `frontend/src/api.js` — dashboard/scan calls go through `authFetch` (Bearer + refresh).
- `frontend/src/dashboard/Dashboard.jsx` — account nav (Profile/Team/Organization),
  user footer + logout, permission-gated actions.
- `frontend/src/dashboard/ScansTable.jsx`, `ScanDetails.jsx` — hide rerun/delete
  without permission.

## 3. Database migrations

`7a1c9e4b2f60` (revises `b2f4a7c9d1e3`) — additive, reversible:
- Creates `users`, `organizations`, `organization_members`, `sessions`,
  `password_resets`, `email_verifications`, `invitations` (with unique/lookup indexes).
- Adds `scans.organization_id`, `scans.user_id`, and index `ix_scans_org_created`.
- Existing anonymous scans keep `organization_id = NULL` and still work.

## 4. API endpoints

**Auth:** `POST /auth/register`, `/auth/login`, `/auth/logout`, `/auth/logout-all`,
`/auth/refresh`, `/auth/forgot-password`, `/auth/reset-password`,
`/auth/verify-email`, `/auth/resend-verification`.
**Profile/sessions:** `GET /me`, `PATCH /me`, `GET /me/sessions`, `DELETE /me/sessions/{id}`.
**Org/team:** `GET/PATCH /org`, `GET /org/members`, `PATCH/DELETE /org/members/{id}`,
`POST/GET /org/invitations`, `DELETE /org/invitations/{id}`, `GET /org/invitations/info`,
`POST /org/invitations/accept`.
**Now protected + org-scoped:** `GET /api/scans`, `GET /api/scans/{id}`,
`DELETE /api/scans/{id}`, `POST /api/scans/{id}/rerun`, `GET /api/dashboard`.

## 5. Security improvements

- bcrypt (cost 12) password hashing; strength policy enforced server-side.
- Short-lived JWT access tokens (memory only) + opaque refresh tokens stored **hashed**;
  **refresh-token rotation** with reuse invalidation; logout / logout-everywhere.
- Refresh cookie: **httpOnly**, **SameSite=strict**, **Secure in production**.
- Login **rate limiting** per email+IP; **no user enumeration** (login & forgot-password
  return generic responses).
- Password reset & change **revoke all sessions**.
- **RBAC** enforced on every product endpoint; scans are **isolated per organization**
  (a scan outside your org is indistinguishable from missing — 404, not 403).
- Invitation acceptance verifies an existing account's password before issuing a session.
- CSRF: cookie is SameSite=strict and all mutations are Bearer-authenticated.

## 6. Authentication architecture

Access = stateless JWT (`sub`, `org`, `role`, 15-min exp). Refresh = stateful opaque
token in an httpOnly cookie, one row per device in `sessions`, rotated each refresh.
The frontend keeps the access token in memory only; on load it silently calls
`/auth/refresh` (cookie) to restore the session, and `authFetch` transparently
refreshes once on any 401. RBAC is driven by the `organization_members.role`
(Owner/Admin/Member/Viewer) via a central permission matrix, enforced by FastAPI
dependencies (the frontend mirrors it only to hide controls). OAuth (Google/GitHub)
is architecturally ready: `users.password_hash` is nullable and the session/JWT layer
is provider-agnostic.

## 7. Remaining work for Phase 6

- OAuth providers (Google, GitHub) on the existing session/JWT foundation.
- Server-side pagination for `/api/scans` (currently capped at 500, filtered client-side).
- Billing / subscriptions / plan-gated features (explicitly deferred).
- Monitoring, scheduled scans, and email scan-reports.
- Multi-org membership + org switching, and owner-transfer.
- Convergence of ARS vs. the 10-signal `overall_score` (carried from Phase 3).

## Verification

- Backend: **93 passed, 1 skipped** (`pytest`) — includes new `test_auth.py` and
  `test_rbac.py` covering registration, login, logout, refresh rotation, password
  reset, email verification, the invite→accept flow, the full role permission matrix,
  protected APIs, and cross-org isolation.
- Frontend: production build ✓. Live browser drive confirmed: guard redirect
  (`/app` → `/login` when signed out), register → private dashboard, Team/Profile/
  Organization pages, logout → home, and sign-in → dashboard.
- Migration applies and downgrades cleanly; dev DB at head `7a1c9e4b2f60`.
- No secrets are logged or committed; no LLM/paid API in any scanner path.
