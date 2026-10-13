# Google Analytics 4 integration (P0)

Lets a customer connect their own GA4 property so AEOMirror can show real inbound
traffic attributed to identifiable AI-assistant referrals, separate from Answer
Tracking (which tests how AI assistants *answer questions about* a site — not real
visitor traffic). Read `app/services/integrations/google_analytics.py`'s module
docstring for the implementation.

This doc has two parts for two different audiences:

- **Part 1** is for the AEOMirror operator — done once, ever, for the whole platform.
  It requires access to Google Cloud Console and the deployment environment.
- **Part 2** is for an AEOMirror customer connecting their own Google Analytics
  account — done per organization, entirely from the AI Traffic page, with no access
  to the server, its environment variables, or any Google Cloud project.

A customer is **never** asked for a Google OAuth Client ID, Client Secret, refresh
token, or encryption key — those are Part 1's responsibility, done once by the
operator. If Part 1 hasn't been completed, the AI Traffic page shows a plain "Google
Analytics connection is temporarily unavailable" state instead of a Connect button —
it does not pretend the feature works.

---

## Part 1 — One-time AEOMirror operator setup

### 1. Create / choose a Google Cloud project

Use an existing project or create one at console.cloud.google.com — any project your
organization controls. This project's OAuth client is what every connected customer
authorizes against, not each customer's own project.

### 2. Enable the required APIs

In the Cloud Console, under **APIs & Services → Library**, enable both:
- **Google Analytics Admin API** — used to list the properties the connected account
  can access (`GET /integrations/google/properties`).
- **Google Analytics Data API** — used to fetch the daily traffic report
  (`runReport`, the sync job).

### 3. Configure the OAuth consent screen

**APIs & Services → OAuth consent screen**:
- User type: External (unless every customer is inside your own Google Workspace).
- Scope: add `https://www.googleapis.com/auth/analytics.readonly` — this is the
  ONLY scope this integration requests, for every customer, always.
- **Publish the app** once ready. While unpublished, Google only allows sign-in from
  accounts explicitly added as test users — fine for development, not for real
  customers. Publishing an app requesting a sensitive scope (`analytics.readonly` is
  sensitive) triggers **Google's app verification review**, which has its own lead
  time (can run to several weeks) — start this well before customers need the
  feature; nothing here can skip or speed up that review.

### 4. Create the OAuth client + register the exact redirect URI

**APIs & Services → Credentials → Create Credentials → OAuth client ID**, type **Web
application**. Add an **Authorized redirect URI** that exactly matches your deployed
backend's callback route:

```
https://<your-api-domain>/integrations/google/callback
```

(No `/api` prefix — this router is mounted at `/integrations/google`, not under
`/api`; check `app/main.py`'s `include_router` call if that ever changes.) Google
rejects any redirect that doesn't match EXACTLY (trailing slash, http vs https, and
subdomain all matter). Copy the generated **Client ID** and **Client Secret**.

### 5. Generate the token encryption key

The ONE secret this integration persists, per connected organization, is that org's
Google refresh token — encrypted at rest with Fernet (`app/core/token_crypto.py`).
Generate ONE key for the whole platform:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Key rotation**: there is no automatic rotation in this V1 — rotating the key makes
every already-connected organization's stored refresh token permanently
undecryptable (they'd see `credential_unavailable` and need to reconnect). Treat it
like any other long-lived secret: store it in your secret manager, change it only
deliberately, and if you ever must rotate it, expect to notify connected customers to
reconnect afterward.

### 6. Set the environment variables

On **both** the `aeomirror-api` web service AND the `aeomirror-worker` service (the
worker runs the actual daily sync job and needs to decrypt/refresh the stored
credential too — see `render.yaml`):

| Variable | Value |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | from step 4 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | from step 4 |
| `GOOGLE_OAUTH_REDIRECT_URI` | exactly the URI registered in step 4 |
| `TOKEN_ENCRYPTION_KEY` | from step 5 — **same value on both services** |

These are the ONLY platform-level secrets this feature needs, and no customer ever
sees or supplies them. Leaving them unset keeps the whole integration disabled
end-to-end:
- `/integrations/google/connect` returns `503` rather than attempting a broken OAuth
  redirect, and
- `/integrations/google/status` returns `{"status": "not_configured"}`, which the
  frontend renders as a plain "temporarily unavailable" message — not a Connect
  button that would fail, and not an unrelated error.

It's safe to deploy with none of this configured; the app starts normally either way
(see `app/config.py`'s `_validate_production` — it only requires a valid
`TOKEN_ENCRYPTION_KEY` once the three `GOOGLE_OAUTH_*` values are actually set).

### 7. Run the database migration

```bash
cd backend && alembic upgrade head
```

Adds three new tables (`oauth_states`, `google_analytics_connections`,
`ga4_traffic_snapshots`) and two additive, nullable columns on the existing
`scheduled_jobs` table. No existing table's existing columns change. On Render this
runs automatically via `preDeployCommand` in `render.yaml`.

### 8. Deploy

Both the `aeomirror-api` web service and the `aeomirror-worker` service need the
updated code and the env vars from step 6 — the worker is what actually executes the
daily `ga4_sync` jobs (reusing the existing `ScheduledJob` queue and worker loop; no
new Render service or infrastructure was added for this).

### 9. Verify it actually works, end to end

Once deployed, connect a real test GA4 property yourself (follow Part 2 below as any
customer would) before telling customers the feature is available. This is the only
way to confirm the redirect URI, consent screen, and worker sync are all actually
wired correctly together — unit tests mock every Google call and can't catch a
misregistered redirect URI or an unpublished consent screen.

---

## Part 2 — Customer: connecting your Google Analytics account

Once your AEOMirror workspace is on a Pro plan and the platform has Google Analytics
enabled, connecting your own account takes a few minutes and requires nothing from
you except a normal Google sign-in.

1. Open **AI Traffic** in the sidebar (`/app/analytics/ai-traffic`).
2. Click **Connect Google Analytics**.
3. Sign in to Google if you aren't already, and grant AEOMirror **read-only** access
   to your Analytics data. AEOMirror never asks for write access and never asks you
   to create a Google Cloud project or supply any Google credentials yourself — it
   only needs your consent.
4. Choose the GA4 property you want tracked from the list AEOMirror shows you (only
   properties your Google account can actually access appear here).
5. The first sync starts automatically. It can take a few minutes to appear — use
   **Sync now** any time to refresh manually.
6. You can **disconnect** at any time from the same page; this revokes AEOMirror's
   access with Google and stops syncing immediately.

**What you'll see, and why:**
- **Unattributable** traffic is real traffic Google Analytics can't attribute to a
  specific, identifiable source — this always includes ordinary Google organic
  search. Google's own API has no way to separate an "AI Overview" click from any
  other organic search click, so AEOMirror never guesses at that split; it's shown
  honestly as unattributable rather than invented.
- If you see "Google Analytics connection is temporarily unavailable," that means
  your AEOMirror workspace's own Google integration hasn't been set up by your
  platform provider yet — it isn't something you can fix from your own Google
  account, and there's nothing misconfigured on your side.
