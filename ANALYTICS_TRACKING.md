# AI Business Impact — first-party analytics tracking

GA4-independent, first-party visitor tracking that answers "how much traffic, and
how many conversions, come from AI assistants?" for a tracked site. This doc covers
registration, installation, verification, attribution, and troubleshooting. For
production environment variables and deploy mechanics, see `DEPLOYMENT.md` — this
doc doesn't repeat that, only the parts specific to this feature.

## 1. Register a tracked site

A "site" here is a `Monitor` row — the same one used for scanning/monitoring. There
is no separate "analytics property": `POST /monitors` (or the **Monitoring** page's
"Add site" form in the dashboard) creates it; the returned `id` is also the
tracking `site_id`. Adding or scanning a site never implies the tracker is
installed — see §3.

## 2. Install the snippet

`GET /monitors/{id}/tracking` (dashboard: **AI Business Impact**, shown automatically
once a site is selected) returns:

```json
{
  "site_id": "...", "snippet": "<script defer src=\"https://<api-host>/t/tracker.js\" data-site-id=\"...\"></script>",
  "tracker_url": "https://<api-host>/t/tracker.js", "collect_url": "https://<api-host>/t/collect",
  "installed": false, "status": "not_installed", "installed_since": null,
  "last_event_at": null, "has_tracking_api_key": false
}
```

Paste the `snippet` value into the site's HTML, ideally just before `</head>`, on
every page you want tracked. `<api-host>` is always `settings.api_base_url` — see
§7 for why this must never be `localhost` in production.

**Example: installing on thedocmirror.com.** Open AI Business Impact for the
`thedocmirror.com` site (registering it first via Monitoring if it isn't already a
tracked site — do not assume a `site_id` seen once in a prior test or log is still
valid or still belongs to the account you're using), copy the `snippet` field
exactly, and paste it into every page template the site renders (header/layout
include, not just the homepage). WordPress/Shopify/Webflow: paste it into the
theme's global `<head>` include. Static/custom sites: paste it directly into each
page's `<head>`.

## 3. How installation is detected — and its real limit

`installed` is **never** assumed from the snippet being generated, a site being
created, or a scan completing — only from a real event reaching `/t/collect`
(`app/api/routes_tracking.py::tracking_status`). There is no separate
install-time ping; the first pageview IS the detection mechanism. `status` gives a
coarser read:

| `status` | Meaning |
|---|---|
| `not_installed` | No event has ever been received for this site. |
| `active` | At least one event, most recently within the last 7 days. |
| `installed_inactive` | At least one event ever, but none in the last 7 days — a heuristic (low traffic looks identical to a removed snippet from the server's point of view), never a confirmed "broken install" diagnosis. |

Use **Check installation status** in the dashboard after pasting the snippet and
loading the site in a browser — it re-fetches this same endpoint.

## 4. Verify event collection manually

1. `curl -s https://<api-host>/t/tracker.js` → the tracker source, HTTP 200.
2. Open the tracked site in a browser with the snippet installed; in DevTools →
   Network, confirm a request to `<api-host>/t/collect` (sendBeacon requests may
   not show a response body, that's expected — check it fired, status is usually
   opaque for `sendBeacon`).
3. Reload **AI Business Impact** — `status` should move from `not_installed` to
   `active`, and `last_event_at` should be recent.
4. For conversions: call `window.aeomirror.track("lead_submitted", {...})` from the
   browser console on the tracked site (after a real or deliberate test submission)
   and confirm it appears, unverified, after a page reload of the dashboard (Pro
   plan — see §6 for what "unverified" means).

Never generate fake production traffic against a real customer's site to "test" —
use a dedicated test site/monitor, or your own. If you genuinely need to verify the
live collection endpoint directly against a real registered domain (e.g. confirming
Origin validation accepts it), append the reserved marker
`?aeomirror_diagnostic=1` (or `&aeomirror_diagnostic=1`) to the test URL —
`aggregation.py::_session_query` excludes any session whose landing URL contains
`aeomirror_diagnostic` from every metric, so it can never be mistaken for real
traffic even though it's a genuine row in the same table. This needs no schema
change and no manual cleanup after a one-off test.

## 5. AI referral attribution — how it works and its limits

`app/services/analytics/classification.py::classify_channel` is deterministic,
evidence-only, and shared with the GA4 classifier's host table
(`app/services/integrations/classification.py::_KNOWN_AI_HOSTS`):

- An explicit `utm_medium` (paid/email/social) always wins over inference.
- A known AI-assistant hostname (ChatGPT, Claude, Gemini, Perplexity, Copilot) —
  via `utm_source` or the real browser referrer — maps to that assistant.
  **Exact-or-subdomain match only, never substring** — `notchatgpt.com` or
  `chatgpt.com.evil.example` never match `chatgpt.com`.
- No referrer and no UTM at all → `direct`.
- A referrer that's the same host as the landing page itself (internal
  navigation) → treated as no referrer.
- Known search engines → `organic_search`; known social platforms → `social`;
  any other identifiable host → `referral`; anything unparseable → `unknown`.

**Real limitation, not a bug:** this can only classify a visit AEOMirror's own
tracker actually recorded, with real referrer/UTM evidence. It cannot measure
every ChatGPT/Gemini conversation, mention, citation, or "answer impression" that
never resulted in a click-through. An AI engine that doesn't pass a referrer (some
in-app browsers strip it) is recorded as `direct`/`unknown`, never guessed as AI.

## 6. Conversions — verified vs. unverified

A conversion sent from the browser (`window.aeomirror.track(...)` or the
`/t/collect` public path) is recorded immediately but **always `verified=False`**.
Only `POST /monitors/{id}/tracking/confirm`, authenticated with a per-site tracking
API key (never the org's own login — generate one under **AI Business Impact →
Generate tracking API key**, called server-to-server by the CUSTOMER'S OWN backend
after a transaction genuinely succeeds), can mark it `verified=True`. The dashboard
shows confirmed and unverified counts as **separate fields** (`leads`/
`confirmed_bookings` vs. `unverified_leads`/`unverified_bookings`) — revenue
(`ai_revenue_cents`) only ever counts verified purchases.

## 7. Required production environment variables

- `API_BASE_URL` — the API's own public HTTPS URL. Baked into every generated
  snippet/`tracker_url`/`collect_url`. **As of 2026-10-26, see the "Known
  deployment drift" note in `DEPLOYMENT.md`** — this had silently fallen back to
  `http://localhost:8000` in production because it was never set on the live
  Render service; `render.yaml` now sets it, but an existing Render service does
  not pick up a new blueprint env var without a manual Sync/redeploy (see
  `DEPLOYMENT.md` step 6).
- `APP_BASE_URL` — the frontend's URL (unrelated to tracking, but commonly
  confused with `API_BASE_URL` — they are never the same value).
- Production fails to **start** if `API_BASE_URL` is still the localhost default
  or not HTTPS (`app/config.py::_validate_production`) — this can no longer ship
  silently.

## 8. Rollback

This feature has no destructive migration and no feature flag — reverting is a
plain `git revert`/redeploy of the relevant commit(s). No data is deleted on
rollback; previously-collected `AnalyticsSession`/`AnalyticsPageview`/
`AnalyticsConversion` rows are untouched regardless of which API version reads them.

## 9. Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Snippet shows `localhost` | `API_BASE_URL` unset/wrong on the LIVE service | `curl https://<the-host-your-frontend-actually-calls>/healthz` — if it 200s but the snippet still shows localhost, the env var genuinely isn't set there; confirm which host the frontend calls by grepping its built JS bundle for `aeomirror`, don't assume the documented custom domain is live. |
| Dashboard always shows "not installed" | Snippet not actually on the live page, or on a different domain than the `site_id` was registered for | View page source on the live site for the exact `<script>` tag; confirm `data-site-id` matches the one the dashboard shows. |
| `/t/collect` returns 403 | Origin/Referer didn't match the site's own registered domain (see `routes_tracking.py::_origin_allowed`) — only fires when a real Origin/Referer header IS present and mismatched; never fires when both are absent. | Confirm the page triggering it is actually the tracked site, not a staging/preview domain under a different hostname. |
| `/t/collect` returns 404 | Unknown `site_id` | Re-copy the snippet from the dashboard; a stale/manually-typed id is never silently accepted. |
| Status stuck on `installed_inactive` | No visits in 7+ days (heuristic, not a confirmed failure) | Confirm the snippet is still in the page source; if it is, this is probably just low traffic. |
