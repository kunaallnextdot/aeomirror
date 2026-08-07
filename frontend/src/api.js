// API client for the AEOMirror free scanner.
//
// Talks to the real backend. By DEFAULT there is no mock fallback: a failed scan
// surfaces a clear, user-facing error instead of a fake score. A local mock is
// available only for offline UI work / demos, and only when explicitly enabled
// with VITE_ENABLE_MOCK_SCANNER=true (defaults to false).
//
// Point VITE_API_URL at your deployed API in production.

// API base URL — comes ONLY from the environment (Vite injects it at build time).
// No hardcoded URL lives in the code:
//   - local dev:  frontend/.env.development (committed) sets it
//   - production: Vercel Environment Variable VITE_API_URL (set in the dashboard)
// See frontend/.env.example and the README for details.
import { authFetch } from "./auth/client.js";

const API = import.meta.env.VITE_API_URL;
const MOCK_ENABLED = import.meta.env.VITE_ENABLE_MOCK_SCANNER === "true";

if (!API && import.meta.env.DEV) {
  // Help catch a misconfigured environment early (dev only; never in production).
  console.warn("[AEOMirror] VITE_API_URL is not set — create frontend/.env.development or set it in your environment.");
}

// Typed error the UI can inspect. `code` is the HTTP status (number) or a short
// string ("network"). `message` is always safe to show to a user — it never
// contains a stack trace, internal path, or raw exception object.
export class ScanError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "ScanError";
    this.code = code;
  }
}

// Map a backend status + optional detail string to a safe, user-facing message.
// The backend's HTTPException `detail` values are curated, non-sensitive strings,
// so we surface them when present; otherwise we fall back to a generic message.
function messageForStatus(status, detail, requestId) {
  const safe = typeof detail === "string" && detail.trim() ? detail.trim() : null;
  if (status === 429) return safe || "Free scan limit reached. Add an email to keep scanning.";
  if (status === 422) return safe || "That URL can't be scanned. It may be a private, local, or invalid address.";
  if (status === 400) return safe || "Could not fetch that site. Check the address and try again.";
  if (status === 404) return safe || "That scan could not be found. It may have expired.";
  if (status === 413) return safe || "That page is too large to scan.";
  if (status === 504) return safe || "That request took too long and timed out. Please try again.";
  if (status >= 500) {
    // Include the server request_id (when present) so a user can quote it in support.
    const base = "The scanner hit an unexpected error. Please try again in a moment.";
    return requestId ? `${base} (ref: ${requestId})` : base;
  }
  return safe || `Scan failed (status ${status}).`;
}

// Read a FastAPI error body and return its `detail` if it is a plain string.
// Pydantic validation errors return a list for `detail`; we ignore those here
// so we never leak raw validation internals into the UI.
async function readDetail(res) {
  try {
    const body = await res.json();
    return body && typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// Like readDetail but also returns the server `request_id` (when present) so a 5xx
// can be quoted in a support email. Reads the body once.
async function readError(res) {
  try {
    const body = await res.json();
    return {
      detail: body && typeof body.detail === "string" ? body.detail : null,
      requestId: body && typeof body.request_id === "string" ? body.request_id : null,
    };
  } catch {
    return { detail: null, requestId: null };
  }
}

// Run a live single-page scan. Resolves with the raw backend ScanResponse, or throws
// a ScanError. Only falls back to the local mock when VITE_ENABLE_MOCK_SCANNER=true.
export async function scanUrl(url) {
  let res;
  try {
    // authFetch attaches the Bearer token when signed in, so the scan is
    // attributed to the user's organization; anonymous otherwise.
    res = await authFetch("/v1/scan", { method: "POST", body: JSON.stringify({ url }) });
  } catch {
    // Network-level failure: backend unreachable, offline, CORS, DNS.
    if (MOCK_ENABLED) return mockScan(url);
    throw new ScanError(
      "Can't reach the scanner service. Make sure the backend is running, then try again.",
      "network",
    );
  }
  if (!res.ok) {
    throw new ScanError(messageForStatus(res.status, await readDetail(res)), res.status);
  }
  return await res.json();
}

/* Build a ScanError from a bulk-scan error response. The bulk endpoint may return a
   structured 422 detail ({message, summary}); carry the summary on the error so the
   UI can show which URLs were skipped. */
async function bulkError(res) {
  let message = null, summary = null;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (body.detail && typeof body.detail === "object") {
      message = body.detail.message; summary = body.detail.summary;
    }
  } catch { /* ignore */ }
  const err = new ScanError(message || messageForStatus(res.status, message), res.status);
  if (summary) err.summary = summary;
  return err;
}

// Start a bulk scan from a JSON list of URLs. Resolves with {scan_id, status, summary}.
export async function bulkScanUrls(urls) {
  let res;
  try { res = await authFetch("/v1/scan/bulk", { method: "POST", body: JSON.stringify({ urls }) }); }
  catch { throw new ScanError("Can't reach the scanner service.", "network"); }
  if (!res.ok) throw await bulkError(res);
  return await res.json();
}

// Start a bulk scan from a .csv/.xlsx upload. Resolves with {scan_id, status, summary}.
export async function bulkScanFile(file) {
  const form = new FormData();
  form.append("file", file);
  let res;
  try { res = await authFetch("/v1/scan/bulk", { method: "POST", body: form }); }
  catch { throw new ScanError("Can't reach the scanner service.", "network"); }
  if (!res.ok) throw await bulkError(res);
  return await res.json();
}

// Fetch a previously stored scan by id (used to restore the dashboard on load).
export async function getScanById(id) {
  let res;
  try {
    res = await authFetch(`/v1/scan/${encodeURIComponent(id)}`);
  } catch {
    throw new ScanError("Can't reach the scanner service.", "network");
  }
  if (!res.ok) {
    throw new ScanError(messageForStatus(res.status, await readDetail(res)), res.status);
  }
  return await res.json();
}

// Submit the Contact & Support form. Resolves with { ok, message } or throws a
// ScanError carrying a user-safe message (422 validation, 429 rate limit, etc.).
export async function submitContact(payload) {
  let res;
  try {
    res = await authFetch("/api/contact", { method: "POST", body: JSON.stringify(payload) });
  } catch {
    throw new ScanError("Can't reach the server right now. Please try again in a moment.", "network");
  }
  if (!res.ok) {
    let msg = null;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
        msg = String(body.detail[0].msg).replace(/^Value error,\s*/, "");
      }
    } catch { /* fall through to a generic message */ }
    if (res.status === 429) msg = msg || "You've sent several messages recently. Please try again a little later.";
    throw new ScanError(msg || "Could not send your message. Please check the form and try again.", res.status);
  }
  return await res.json();
}

// Best-effort lead capture. Returns true on success, false otherwise; never
// throws, because a failed capture must not block the scanner UX.
export async function captureLead(email, url) {
  try {
    const res = await authFetch("/v1/lead", { method: "POST", body: JSON.stringify({ email, url }) });
    return res.ok;
  } catch {
    return false;
  }
}

/* =====================================================================
   Dashboard API (Phase 4). Single request helper; all throw ScanError on
   failure so the dashboard can render consistent error states.
   ===================================================================== */
// Client-side ceiling, slightly above the server's content_insight_budget_seconds (55s)
// so a slow "Analyze content" call is aborted as a timeout — not left to an upstream
// connection kill that would look like an unreachable backend.
const REQUEST_TIMEOUT_MS = 60000;

async function request(path, opts = {}) {
  let res;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    res = await authFetch(path, { ...opts, signal: ctrl.signal });  // Bearer token + refresh-on-401
  } catch (e) {
    if (e?.name === "AbortError") {
      // Our timeout fired (or the request was aborted) — report it honestly as a
      // timeout, distinct from a genuine connection failure.
      throw new ScanError("That request took too long and timed out. Please try again.", "timeout");
    }
    // A rejected fetch here is a genuine transport failure (offline, DNS, CORS, server
    // down). Real HTTP errors — including 500 — arrive as a response and are handled
    // below, so this branch no longer masks a server error as "backend unreachable".
    throw new ScanError("Can't reach the scanner service. Make sure the backend is running.", "network");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const { detail, requestId } = await readError(res);
    throw new ScanError(messageForStatus(res.status, detail, requestId), res.status);
  }
  return res.status === 204 ? null : await res.json();
}

export function getDashboard() {
  return request(`/api/dashboard`);
}

export function getScans(params = {}) {
  const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  const qs = new URLSearchParams(clean).toString();
  return request(`/api/scans${qs ? `?${qs}` : ""}`);
}

export function getScanDetail(id) {
  return request(`/api/scans/${encodeURIComponent(id)}`);
}

// Lightweight progress poll for a background (full-site) scan.
// Returns { status: "pending"|"running"|"completed"|"failed", progress }.
export function getScanStatus(id) {
  return request(`/api/scans/${encodeURIComponent(id)}/status`);
}

// Compare two scans. Server-metered on the Free plan (throws ScanError code 402
// when the monthly comparison quota is exhausted). Returns { a, b } full reports.
export function compareScans(aId, bId) {
  return request(`/api/compare`, { method: "POST", body: JSON.stringify({ a_id: aId, b_id: bId }) });
}

export function deleteScan(id) {
  return request(`/api/scans/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function rerunScan(id) {
  return request(`/api/scans/${encodeURIComponent(id)}/rerun`, { method: "POST" });
}

/* =====================================================================
   Reports (Phase 6). The full rule-based AI-visibility report + exports.
   Downloads go through authFetch (Bearer token) and are turned into a blob so
   the browser saves them — a plain <a href> link could not send the token.
   ===================================================================== */
export function getReport(scanId) {
  return request(`/reports/${encodeURIComponent(scanId)}`);
}

// Lightweight unlock check for a report's exports: resolves { unlocked: bool }.
export function getReportAccess(scanId) {
  return request(`/reports/${encodeURIComponent(scanId)}/access`);
}

/* Public report share links (owner-side, authenticated). Create throws ScanError 402
   when the org's active-share cap is reached. List/create return { token, path, ... }
   so the owner can re-copy the URL (no show-once). */
export function createShare(scanId) {
  return request(`/reports/${encodeURIComponent(scanId)}/share`, { method: "POST" });
}
export function listShares(scanId) {
  return request(`/reports/${encodeURIComponent(scanId)}/shares`);
}
export function revokeShare(shareId) {
  return request(`/reports/shares/${encodeURIComponent(shareId)}`, { method: "DELETE" });
}

/* PUBLIC read path for a shared report — DELIBERATELY a plain fetch, NOT authFetch /
   request(): no Authorization header (a signed-in viewer must not leak their token onto
   a public request) and no refresh-on-401 (a signed-out viewer must trigger no auth
   cycle). credentials:"omit" so no cookie is sent either. Any miss is a flat 404. */
export async function getPublicReport(token) {
  let res;
  try {
    res = await fetch(`${API}/public/reports/${encodeURIComponent(token)}`, {
      method: "GET", credentials: "omit", headers: { Accept: "application/json" },
    });
  } catch {
    throw new ScanError("Can't reach the report service. Please try again.", "network");
  }
  if (res.status === 404) {
    throw new ScanError("This shared report link is invalid, expired, or has been revoked.", 404);
  }
  if (!res.ok) throw new ScanError(messageForStatus(res.status, null), res.status);
  return res.json();
}

/* AI Content Insights (Pro-only, per page). Reading cached insights is open to any
   member; generating is Pro-gated (POST throws ScanError 402 for Free, 503 when the
   model is unavailable). */
export function getContentInsights(scanId) {
  return request(`/api/scans/${encodeURIComponent(scanId)}/content-insights`);
}
export function analyzeContent(scanId, pageUrl) {
  return request(`/api/scans/${encodeURIComponent(scanId)}/content-insights`, {
    method: "POST", body: JSON.stringify(pageUrl ? { page_url: pageUrl } : {}),
  });
}

/* =====================================================================
   Monitoring (Phase 7). Monitors, alerts, and history — all org-scoped.
   ===================================================================== */
export function createMonitor({ url, frequency, name }) {
  return request(`/monitors`, { method: "POST", body: JSON.stringify({ url, frequency, name }) });
}
export function listMonitors() { return request(`/monitors`); }
export function getMonitor(id) { return request(`/monitors/${encodeURIComponent(id)}`); }
export function updateMonitor(id, patch) {
  return request(`/monitors/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) });
}
export function deleteMonitor(id) {
  return request(`/monitors/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export function runMonitor(id) {
  return request(`/monitors/${encodeURIComponent(id)}/run`, { method: "POST" });
}
export function listAlerts(params = {}) {
  const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  const qs = new URLSearchParams(clean).toString();
  return request(`/alerts${qs ? `?${qs}` : ""}`);
}
export function acknowledgeAlert(id) {
  return request(`/alerts/${encodeURIComponent(id)}/acknowledge`, { method: "POST" });
}
export function getMonitorHistory(id) {
  return request(`/history/${encodeURIComponent(id)}`);
}

/* =====================================================================
   Admin platform (Phase 8). All under /admin; require a platform admin.
   ===================================================================== */
function qs(params = {}) {
  const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  const s = new URLSearchParams(clean).toString();
  return s ? `?${s}` : "";
}
export const admin = {
  dashboard: () => request(`/admin/dashboard`),
  analytics: () => request(`/admin/analytics`),
  system: () => request(`/admin/system`),
  logs: (params) => request(`/admin/logs${qs(params)}`),
  getSettings: () => request(`/admin/settings`),
  updateSettings: (body) => request(`/admin/settings`, { method: "PATCH", body: JSON.stringify(body) }),

  users: (params) => request(`/admin/users${qs(params)}`),
  userActivity: (id) => request(`/admin/users/${encodeURIComponent(id)}/activity`),
  suspendUser: (id) => request(`/admin/users/${encodeURIComponent(id)}/suspend`, { method: "POST" }),
  activateUser: (id) => request(`/admin/users/${encodeURIComponent(id)}/activate`, { method: "POST" }),
  verifyEmail: (id) => request(`/admin/users/${encodeURIComponent(id)}/verify-email`, { method: "POST" }),
  resetPassword: (id) => request(`/admin/users/${encodeURIComponent(id)}/reset-password`, { method: "POST" }),
  deleteUser: (id) => request(`/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" }),

  orgs: (params) => request(`/admin/organizations${qs(params)}`),
  orgDetail: (id) => request(`/admin/organizations/${encodeURIComponent(id)}`),
  deleteOrg: (id) => request(`/admin/organizations/${encodeURIComponent(id)}`, { method: "DELETE" }),

  scans: (params) => request(`/admin/scans${qs(params)}`),
  scanReport: (id) => request(`/admin/scans/${encodeURIComponent(id)}`),
  scanLogs: (id) => request(`/admin/scans/${encodeURIComponent(id)}/logs`),
  deleteScan: (id) => request(`/admin/scans/${encodeURIComponent(id)}`, { method: "DELETE" }),
  rerunScan: (id) => request(`/admin/scans/${encodeURIComponent(id)}/rerun`, { method: "POST" }),

  monitors: (params) => request(`/admin/monitors${qs(params)}`),
  pauseMonitor: (id) => request(`/admin/monitors/${encodeURIComponent(id)}/pause`, { method: "POST" }),
  resumeMonitor: (id) => request(`/admin/monitors/${encodeURIComponent(id)}/resume`, { method: "POST" }),
  runMonitor: (id) => request(`/admin/monitors/${encodeURIComponent(id)}/run`, { method: "POST" }),
  deleteMonitor: (id) => request(`/admin/monitors/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // Support Inbox (contacts)
  contacts: (params) => request(`/admin/contacts${qs(params)}`),
  contactDetail: (id) => request(`/admin/contacts/${encodeURIComponent(id)}`),
  setContactStatus: (id, status) => request(`/admin/contacts/${encodeURIComponent(id)}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  deleteContact: (id) => request(`/admin/contacts/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

/* =====================================================================
   Billing (Phase 9). Plans, checkout, subscription, invoices, payments.
   ===================================================================== */
export const billing = {
  plans: () => request(`/billing/plans`),
  subscription: () => request(`/billing/subscription`),
  checkout: (body) => request(`/billing/checkout`, { method: "POST", body: JSON.stringify(body) }),
  completeDev: (reference) => request(`/billing/checkout/complete`, { method: "POST", body: JSON.stringify({ reference }) }),
  cancel: () => request(`/billing/subscription/cancel`, { method: "PATCH" }),
  resume: () => request(`/billing/subscription/resume`, { method: "PATCH" }),
  payments: () => request(`/billing/payments`),
  invoices: () => request(`/billing/invoices`),
};

// Start a checkout for `planCode` (optionally for a scan). In dev mode the backend
// returns a completion reference we finalize immediately; in production we redirect
// to the provider's hosted checkout.
export async function startCheckout(planCode, scanId) {
  const res = await billing.checkout({ plan_code: planCode, scan_id: scanId });
  if (res.dev_mode) {
    await billing.completeDev(res.reference);
    return { completed: true };
  }
  window.location.href = res.checkout_url;
  return { redirected: true };
}

export async function downloadReport(scanId, format) {
  let res;
  try {
    res = await authFetch(`/reports/${encodeURIComponent(scanId)}/${format}`);
  } catch {
    throw new ScanError("Can't reach the report service.", "network");
  }
  if (!res.ok) throw new ScanError(messageForStatus(res.status, await readDetail(res)), res.status);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  const filename = m ? m[1] : `aeomirror-report.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

/* =====================================================================
   Local mock (mirrors the backend rubric) — DISABLED unless
   VITE_ENABLE_MOCK_SCANNER=true. Kept for offline UI work and demos.
   It is never reached on the normal real-scanner path.
   ===================================================================== */
const FAMILIES = [
  { id: "crawler_access", label: "Crawler access", weight: 25 },
  { id: "render_parity", label: "Render parity", weight: 20 },
  { id: "schema", label: "Schema validity", weight: 20 },
  { id: "structure", label: "Structure", weight: 15 },
  { id: "extractability", label: "Extractability", weight: 12 },
  { id: "freshness", label: "Freshness", weight: 8 },
];
const CHECKS = {
  crawler_access: [
    { id: "gptbot_allowed", label: "GPTBot access", weight: 7, fix_hint: "Allow GPTBot in robots.txt" },
    { id: "claudebot_allowed", label: "ClaudeBot access", weight: 7, fix_hint: "Allow ClaudeBot in robots.txt" },
    { id: "perplexitybot_allowed", label: "PerplexityBot access", weight: 5, fix_hint: "Allow PerplexityBot in robots.txt" },
    { id: "google_extended_ok", label: "Google-Extended access", weight: 3, fix_hint: "Do not block Google-Extended" },
    { id: "no_blanket_disallow", label: "No blanket block", weight: 3, fix_hint: "Remove site-wide Disallow: /" },
  ],
  render_parity: [
    { id: "has_real_text", label: "Readable text present", weight: 10, fix_hint: "Server-render main content" },
    { id: "content_in_raw_html", label: "Content in source", weight: 7, fix_hint: "Raise text-to-HTML ratio via SSR" },
    { id: "not_js_shell", label: "Not an empty shell", weight: 3, fix_hint: "Enable SSR or pre-render" },
  ],
  schema: [
    { id: "has_jsonld", label: "Structured data present", weight: 6, fix_hint: "Add JSON-LD structured data" },
    { id: "has_org_schema", label: "Organization schema", weight: 6, fix_hint: "Add Organization JSON-LD" },
    { id: "has_type_schema", label: "Content-type schema", weight: 5, fix_hint: "Add Product / Article / FAQ schema" },
    { id: "schema_parses", label: "Schema is valid JSON", weight: 3, fix_hint: "Fix malformed JSON-LD" },
  ],
  structure: [
    { id: "has_title", label: "Page title", weight: 3, fix_hint: "Add a 10 to 65 char title" },
    { id: "single_h1", label: "Single H1", weight: 3, fix_hint: "Use one clear H1" },
    { id: "has_meta_description", label: "Meta description", weight: 3, fix_hint: "Add a meta description" },
    { id: "has_canonical", label: "Canonical URL", weight: 2, fix_hint: "Add a canonical link" },
    { id: "has_sitemap_ref", label: "Sitemap", weight: 2, fix_hint: "Publish sitemap.xml" },
    { id: "has_llms_txt", label: "llms.txt", weight: 2, fix_hint: "Add llms.txt (emerging convention)" },
  ],
  extractability: [
    { id: "semantic_html", label: "Semantic HTML", weight: 4, fix_hint: "Wrap content in main / article" },
    { id: "has_faq_or_qa", label: "Question-answer content", weight: 4, fix_hint: "Add an FAQ section" },
    { id: "scannable_structure", label: "Scannable structure", weight: 4, fix_hint: "Break into paragraphs and lists" },
  ],
  freshness: [
    { id: "has_date_modified", label: "Date in schema", weight: 4, fix_hint: "Add dateModified to schema" },
    { id: "has_visible_date", label: "Visible date", weight: 4, fix_hint: "Show a visible updated date" },
  ],
};
const MULT = { pass: 1, warn: 0.5, fail: 0 };
const DEMO = {
  "example-shop.com": { bias: 0.28, force: { gptbot_allowed: "fail", claudebot_allowed: "fail", has_real_text: "fail", not_js_shell: "fail" } },
  "brewlab.io": { bias: 0.86, force: {} },
};
function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
function rng(seed) { let s = seed; return () => (s = Math.imul(s, 48271) % 2147483647) / 2147483647; }
function norm(u) { return u.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/.*$/, ""); }

function mockScan(rawUrl) {
  const domain = norm(rawUrl) || "your-site.com";
  const demo = DEMO[domain];
  const rand = rng(hash(domain));
  const bias = demo ? demo.bias : 0.4 + rand() * 0.4;
  const force = demo ? demo.force : {};
  const families = FAMILIES.map((fam) => {
    const checks = CHECKS[fam.id].map((c) => {
      let status = force[c.id];
      if (!status) { const r = rand(); status = r < bias ? "pass" : r < bias + (1 - bias) * 0.45 ? "warn" : "fail"; }
      return { ...c, status, detail: "", earned: c.weight * MULT[status] };
    });
    return { ...fam, checks, earned: Math.round(checks.reduce((a, c) => a + c.earned, 0) * 10) / 10 };
  });
  const ars = Math.max(0, Math.min(100, Math.round(families.reduce((a, f) => a + f.earned, 0))));
  const top_issues = families.flatMap((f) => f.checks.filter((c) => c.status !== "pass").map((c) => ({ ...c, family: f.label })))
    .sort((a, b) => MULT[a.status] - MULT[b.status] || b.weight - a.weight).slice(0, 5);
  const crawlers = families.find((f) => f.id === "crawler_access").checks
    .filter((c) => ["gptbot_allowed", "claudebot_allowed", "perplexitybot_allowed", "google_extended_ok"].includes(c.id))
    .map((c) => ({ id: c.id, label: c.label, status: c.status }));
  return { scan_id: "mock-" + hash(domain), url: rawUrl, domain, ars, rubric_version: "2026.07.1", families, top_issues, crawlers, remaining_free_scans: 2, _mock: true };
}
