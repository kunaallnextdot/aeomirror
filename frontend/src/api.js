// API client for the AEOMirror free scanner.
//
// Talks to the real backend. By DEFAULT there is no mock fallback: a failed scan
// surfaces a clear, user-facing error instead of a fake score. A local mock is
// available only for offline UI work / demos, and only when explicitly enabled
// with VITE_ENABLE_MOCK_SCANNER=true (defaults to false).
//
// Point VITE_API_URL at your deployed API in production.

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const MOCK_ENABLED = import.meta.env.VITE_ENABLE_MOCK_SCANNER === "true";

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
function messageForStatus(status, detail) {
  const safe = typeof detail === "string" && detail.trim() ? detail.trim() : null;
  if (status === 429) return safe || "Free scan limit reached. Add an email to keep scanning.";
  if (status === 422) return safe || "That URL can't be scanned. It may be a private, local, or invalid address.";
  if (status === 400) return safe || "Could not fetch that site. Check the address and try again.";
  if (status === 404) return safe || "That scan could not be found. It may have expired.";
  if (status === 413) return safe || "That page is too large to scan.";
  if (status >= 500) return "The scanner hit an unexpected error. Please try again in a moment.";
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

// Run a live scan. Resolves with the raw backend ScanResponse, or throws a
// ScanError. Only falls back to the local mock when VITE_ENABLE_MOCK_SCANNER=true.
export async function scanUrl(url) {
  let res;
  try {
    res = await fetch(`${API}/v1/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
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

// Fetch a previously stored scan by id (used to restore the dashboard on load).
export async function getScanById(id) {
  let res;
  try {
    res = await fetch(`${API}/v1/scan/${encodeURIComponent(id)}`);
  } catch {
    throw new ScanError("Can't reach the scanner service.", "network");
  }
  if (!res.ok) {
    throw new ScanError(messageForStatus(res.status, await readDetail(res)), res.status);
  }
  return await res.json();
}

// Best-effort lead capture. Returns true on success, false otherwise; never
// throws, because a failed capture must not block the scanner UX.
export async function captureLead(email, url) {
  try {
    const res = await fetch(`${API}/v1/lead`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, url }),
    });
    return res.ok;
  } catch {
    return false;
  }
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
