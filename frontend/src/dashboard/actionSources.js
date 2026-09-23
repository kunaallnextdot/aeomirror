/* Action Center V2 — normalization layer over EXISTING backend intelligence. Pure
   functions only: no scoring, no LLM, no network. Every field below is read from a
   real backend object (report.recommendations / report.technical_seo /
   report.content_intelligence / report.phase4 (schema/entity/links) /
   report.crawl_graph / the answer-simulation summary) — nothing here computes a new
   priority, invents evidence, or classifies a question as off-topic itself (that
   classification already happened deterministically in the Answer Simulator; this
   file only ever reads the resulting COUNT, never individual question text, so an
   off-topic/INSUFFICIENT_EVIDENCE question can never leak through as a fabricated
   "content opportunity" — see normalizeAnswerSimulation).

   PRIORITY_RANK / SEVERITY_RANK reuse the SAME Critical/High/Medium/Low vocabulary
   every source already uses (recommendations.priority, technical_seo issue.severity,
   content_intelligence cluster.severity, crawl_graph issue.severity) — never a new
   numeric score.

   `verifySignalId` (added to ActionItems where the source object carries a real
   `related_recommendation_id` — recommendations always, phase4 schema/entity/links
   when present) lets the existing Fix Verification badge (VerificationBadge.jsx) key
   off the SAME scanner signal id `POST /api/verifications` already understands.
   Sources with no such field (technical_seo issues not covered by a recommendation,
   content_intelligence, crawl_graph, question mining) simply omit it — Verify is
   never guessed at.

   Dedup against `recommendations[]` (a single underlying issue should have ONE
   stable identity) is applied via each source's own real `related_recommendation_id`
   field wherever it exists — technical_seo (via TECH_SEO_COVERED_BY_RECOMMENDATION,
   the mature recommendation card wins) AND, as of this pass, phase4 schema/entity/
   links (same rule: when a recommendation with that exact id already exists on this
   report, the phase4 finding is suppressed rather than shown a second time). Sources
   with no such field (content_intelligence, crawl_graph, questions) are never
   deduped against recommendations — there is nothing real to key the dedup on.

   Every destination link below points at the real per-section anchor already
   rendered by ReportView.jsx (`#rep-schema`, `#rep-technical-seo`, etc.) so clicking
   "Review schema"/"View affected pages"/etc. actually lands on that section instead
   of the top of the report. */

export const PRIORITY_RANK = { Critical: 0, High: 1, Medium: 2, Low: 3 };

// Only used to break ties when priority is equal across sources — documented per the
// ticket's own suggested order. Never overrides an existing priority.
const SOURCE_ORDER = {
  Recommendations: 0, "Technical SEO": 1, "AI Visibility": 2, "Content Intelligence": 3,
  "Schema Intelligence": 4, "Entity Intelligence": 5, "Internal Linking": 6, "Crawl Graph": 7,
  "Question Opportunities": 8,
};

// A recommendation's signal_id that structurally already covers the same root cause
// as one of technical_seo's per-URL issue codes — both systems can independently
// surface e.g. a robots problem (the scanner's own "robots" signal AND a per-URL
// ROBOTS_BLOCKED finding). When BOTH exist for the same scan, the more mature
// recommendation card (full fix_template/evidence/why-it-matters) wins and the
// technical_seo card for that code is suppressed — per the ticket's explicit
// "prefer the mature recommendation card" rule. Deliberately small and explicit
// (a documented editorial mapping of stable ids), never fuzzy text matching.
// Exported: ReportView.jsx's own Technical SEO section reuses this SAME map (never a
// second copy) to decide when a per-code issue can offer "Verify after re-scan"
// (only when the covering recommendation actually exists on this report).
export const TECH_SEO_COVERED_BY_RECOMMENDATION = {
  robots: ["ROBOTS_BLOCKED"],
  sitemap: ["NOT_IN_SITEMAP", "SITEMAP_URL_NOT_CRAWLED"],
  metadata: ["NOINDEX_META", "NOINDEX_XROBOTS", "CANONICAL_INVALID", "CANONICAL_REDIRECT"],
};

export const TECH_ISSUE_PROBLEM = {
  ERROR_5XX: "These URLs return a server error and can't currently be crawled.",
  ERROR_4XX: "These URLs return a client error and can't currently be indexed.",
  CANONICAL_4XX: "These URLs canonicalize to a target that returns a 4xx error.",
  CANONICAL_5XX: "These URLs canonicalize to a target that returns a 5xx error.",
  REDIRECT_LOOP: "These URLs redirect in a loop and never resolve.",
  NOINDEX_META: "These URLs are marked noindex via meta robots and won't be indexed.",
  NOINDEX_XROBOTS: "These URLs are marked noindex via the X-Robots-Tag header and won't be indexed.",
  ROBOTS_BLOCKED: "These URLs are blocked from crawling by robots.txt.",
  REDIRECT_CHAIN: "These URLs redirect through multiple hops before resolving.",
  REDIRECT_TO_ERROR: "These redirects lead to an error page.",
  CANONICAL_INVALID: "These URLs have a malformed canonical tag.",
  CANONICAL_REDIRECT: "These URLs canonicalize to a target that itself redirects.",
  NOT_IN_SITEMAP: "These indexable URLs are missing from the sitemap.",
  SITEMAP_URL_NOT_CRAWLED: "These sitemap URLs were not reachable during this scan.",
};
export const TECH_ISSUE_FIX = {
  ERROR_5XX: "Investigate and fix the failing URLs.",
  ERROR_4XX: "Investigate and fix the failing URLs.",
  CANONICAL_4XX: "Review canonical tag configuration.",
  CANONICAL_5XX: "Review canonical tag configuration.",
  REDIRECT_LOOP: "Review the redirect configuration for these URLs.",
  NOINDEX_META: "Review the affected indexability directives.",
  NOINDEX_XROBOTS: "Review the affected indexability directives.",
  ROBOTS_BLOCKED: "Review the affected indexability directives.",
  REDIRECT_CHAIN: "Review the redirect configuration for these URLs.",
  REDIRECT_TO_ERROR: "Review the redirect configuration for these URLs.",
  CANONICAL_INVALID: "Review canonical tag configuration.",
  CANONICAL_REDIRECT: "Review canonical tag configuration.",
  NOT_IN_SITEMAP: "Review sitemap coverage for these URLs.",
  SITEMAP_URL_NOT_CRAWLED: "Review sitemap coverage for these URLs.",
};

// The ticket's own literal specified copy per content_intelligence.recommended_action
// code — direct reuse of ticket-mandated wording, not invented.
const CONTENT_ACTION_FIX = {
  CONSOLIDATE: "Consolidate these pages.",
  DIFFERENTIATE: "Differentiate the pages by search intent and content focus.",
  REVIEW_CANONICAL: "Review canonical targeting.",
};

// Phase I: near_duplicate and potential_cannibalization are computed from genuinely
// different evidence strength (see backend `reports/content_intelligence.py::_pair_type`
// — near_duplicate is a single strong content-similarity measurement; potential_
// cannibalization requires 2+ weaker supporting signals, none of them proof on its own).
// Both can carry the same "High" severity, so without this text a user has no way to
// tell them apart. Cautious wording only — never claims confirmed cannibalization,
// since the engine itself never establishes that, only an inference worth reviewing.
export const CONTENT_CLUSTER_TYPE_CAVEAT = {
  near_duplicate: "Strong signal: these pages share a high degree of near-identical text.",
  potential_cannibalization: "An inference, not proof — multiple weaker signals (similar "
    + "titles/headings, canonical setup) suggest these pages may compete for the same search "
    + "intent, not a confirmed conflict.",
};

// `phase4.entity.missing_signals[]` is an array of check keys (real field names from
// `reports/phase4.py`'s `_ENTITY_CHECKS`), not per-item objects — unlike schema's
// `missing_types`/links' `issues`, which already carry their own why/fix text. This is
// the same small-editorial-label pattern as TECH_ISSUE_PROBLEM/FIX above: the keys
// themselves are real data, this only supplies a human-readable label for each.
export const ENTITY_SIGNAL_LABEL = {
  has_entity_schema: "No Organization/entity schema detected",
  has_name: "Entity schema is missing a name",
  has_url: "Entity schema is missing a url",
  has_logo: "Entity schema is missing a logo",
  has_sameas: "No sameAs relationship detected",
  has_website_schema: "No WebSite schema detected",
};

// Crawl graph issue codes -> a short card title, distinct from `iss.label` (which is
// already a full, count-bearing sentence — e.g. "2 crawled pages have zero inbound
// internal links" — used as the PROBLEM text). Without this, the card's bold title
// and its problem paragraph would repeat the exact same sentence verbatim.
export const CRAWL_ISSUE_TITLE = {
  ORPHAN_PAGE: "Orphan pages detected",
  DISCONNECTED_PAGE: "Disconnected pages detected",
  DEEP_PAGE: "Pages buried deep in the crawl path",
  HIGH_OUTBOUND_PAGE: "Pages with unusually high outbound links",
  WEAK_INBOUND_COVERAGE: "Weak internal-link coverage",
  GENERIC_ANCHOR_PATTERN: "Generic anchor text pattern",
  EMPTY_ANCHOR_PATTERN: "Links with no anchor text",
};

// Crawl graph issue codes -> a short fix instruction. Severity/affected_urls
// themselves already come straight from `crawl_graph.issues[]` (see
// `normalizeCrawlGraphIssues`) — this only supplies the "how to fix" half, mirroring
// TECH_ISSUE_FIX's pattern for the technical-SEO source.
export const CRAWL_ISSUE_FIX = {
  ORPHAN_PAGE: "Add internal links from other pages to these URLs.",
  DISCONNECTED_PAGE: "Link to these pages from somewhere reachable in your site's crawl path.",
  DEEP_PAGE: "Bring these pages closer to the homepage via fewer internal-link hops.",
  HIGH_OUTBOUND_PAGE: "Review whether every outbound link on these pages is necessary.",
  WEAK_INBOUND_COVERAGE: "Add more internal links pointing to these pages.",
  GENERIC_ANCHOR_PATTERN: "Replace generic anchor text with descriptive text naming the destination.",
  EMPTY_ANCHOR_PATTERN: "Give every link descriptive anchor text.",
};

// Crawl graph issue codes -> a one-line explanation of what the code actually means.
// ORPHAN_PAGE and DISCONNECTED_PAGE are easy to conflate but are NOT the same thing
// (see `reports/crawl_graph.py`'s own `_issue_summary`): an orphan has zero inbound
// internal links from any other crawled page; a disconnected page may still have
// inbound links but isn't reachable by following links from the crawl seed. Reused by
// ReportView's Crawl Graph section so this distinction is explained once, not per-card.
export const CRAWL_ISSUE_WHY = {
  ORPHAN_PAGE: "No other crawled page links to these URLs — search engines and AI crawlers can only reach them if you link to them directly or list them in your sitemap.",
  DISCONNECTED_PAGE: "These URLs aren't reachable by following links from the crawl seed, even though another page may still link to them — crawlers that don't follow that path may never find them.",
  DEEP_PAGE: "Pages many hops from the homepage are generally crawled and weighted less than pages close to it.",
  HIGH_OUTBOUND_PAGE: "A very high number of outbound links can dilute the link equity passed to any one of them.",
  WEAK_INBOUND_COVERAGE: "Pages with very few internal links pointing to them are harder for crawlers to discover and rank.",
  GENERIC_ANCHOR_PATTERN: "Generic anchor text (e.g. \"click here\") gives crawlers no context about the linked page's topic.",
  EMPTY_ANCHOR_PATTERN: "A link with no anchor text gives crawlers no context about the linked page's topic.",
};

// `hash` is one of ReportView.jsx's own real `id="rep-*"` section anchors — passing
// one makes the link land on that section instead of the top of the report. Omitted
// entirely (never a made-up id) for sources with no single-section home.
function reportDest(scanId, hash) {
  const base = `/app/report?scan=${encodeURIComponent(scanId)}`;
  return { to: hash ? `${base}#${hash}` : base, label: "Open report" };
}

/** report.recommendations[] -> ActionItem. Reuses RecommendationCard verbatim on
 * expand (kind: "recommendation") — never a re-rendered fix_template. */
export function normalizeRecommendation(r, scanId) {
  const fx = r.fix_template || {};
  const evSource = r.evidence?.findings || r.evidence || {};
  const evidencePreview = Object.entries(evSource)
    .filter(([k]) => k !== "detected_types").slice(0, 2)
    .map(([k, v]) => `${k}: ${formatEvidenceValue(v)}`);
  return {
    id: r.id, kind: "recommendation", source: "Recommendations", type: r.id,
    title: r.issue_title, problem: r.description, priority: r.priority,
    evidencePreview, fixText: fx.recommended_fix?.[0] || fx.expected_outcome || null,
    destination: { to: `/app/scans/${encodeURIComponent(scanId)}`, label: "Open report" },
    verifySignalId: r.id,
    raw: r,
  };
}

/** report.content_intelligence.clusters[] -> ActionItem, skipping KEEP_SEPARATE
 * (explicitly not a problem per the ticket) and any cluster whose type has no
 * evidence-backed action. */
export function normalizeContentCluster(c, scanId) {
  if (c.recommended_action === "KEEP_SEPARATE") return null;
  const pageUrls = (c.pages || []).map((p) => p.url);
  // `problem` must be the actual diagnosed finding (real evidence — e.g. "Content
  // similarity: 45% (word-trigram overlap)"), never `c.recommendation` (that's
  // fix-flavored text, already used for `fixText` below via CONTENT_ACTION_FIX —
  // using it for BOTH problem and fix meant the card never showed real evidence as
  // its "what's wrong" line at all). Uses the cluster's own first real evidence
  // line — never a synthesized/invented sentence.
  const problem = (c.evidence || [])[0] || null;
  return {
    id: c.id, kind: "content_cluster", source: "Content Intelligence", type: c.type,
    title: c.label, problem, priority: c.severity,
    evidencePreview: [...(c.evidence || []), ...pageUrls].slice(0, 3),
    fullEvidence: [...(c.evidence || []), ...pageUrls],
    fixText: CONTENT_ACTION_FIX[c.recommended_action] || null,
    why: CONTENT_CLUSTER_TYPE_CAVEAT[c.type] || null,
    destination: { ...reportDest(scanId, "rep-content-intelligence"), label: "Review content" },
    raw: c,
  };
}

/** report.content_intelligence.thin_pages[] -> ONE aggregate ActionItem (never one
 * card per page — that would overwhelm the list for what is a single finding). */
export function normalizeThinContent(thinPages, scanId) {
  if (!thinPages || thinPages.length === 0) return null;
  const n = thinPages.length;
  return {
    id: `content_thin:${scanId}`, kind: "thin_content", source: "Content Intelligence",
    type: "thin_content",
    title: `${n} page${n === 1 ? "" : "s"} have unusually low content depth`,
    problem: `${n} page${n === 1 ? "" : "s"} fall well below the site's own median word count.`,
    priority: "Medium",
    evidencePreview: thinPages.slice(0, 3).map((p) =>
      `${p.url}: ${p.word_count} words (site median ${Math.round(p.site_median_word_count)})`),
    fullEvidence: thinPages.map((p) =>
      `${p.url}: ${p.word_count} words (site median ${Math.round(p.site_median_word_count)})`),
    fixText: "Review whether each page provides enough useful information for its intended topic.",
    destination: { ...reportDest(scanId, "rep-content-intelligence"), label: "Review content" },
    raw: thinPages,
  };
}

/** report.technical_seo.issues[] -> ActionItem[], excluding any code already covered
 * by an existing recommendation for the same signal_id (see
 * TECH_SEO_COVERED_BY_RECOMMENDATION — the mature recommendation card wins). */
export function normalizeTechnicalIssues(issues, scanId, recommendationIds) {
  const coveredCodes = new Set();
  for (const [signalId, codes] of Object.entries(TECH_SEO_COVERED_BY_RECOMMENDATION)) {
    if (recommendationIds.has(signalId)) codes.forEach((c) => coveredCodes.add(c));
  }
  return (issues || [])
    .filter((i) => !coveredCodes.has(i.code))
    .map((i) => ({
      id: `tech:${i.code}`, kind: "technical_issue", source: "Technical SEO", type: i.code,
      title: i.label, problem: TECH_ISSUE_PROBLEM[i.code] || i.label,
      priority: i.severity,
      evidencePreview: (i.affected_urls || []).slice(0, 3),
      fullEvidence: i.affected_urls || [],
      fixText: TECH_ISSUE_FIX[i.code] || null,
      destination: { ...reportDest(scanId, "rep-technical-seo"), label: "View affected pages" },
      raw: i,
    }));
}

/** The Answer Simulator's most recently PERSISTED run summary (never triggers a new
 * run, never touches individual question text — see module docstring) ->
 * at most ONE aggregate ActionItem when real INSUFFICIENT_EVIDENCE questions exist. */
export function normalizeAnswerSimulation(sim, scanId) {
  if (!sim || !sim.available) return null;
  const breakdown = sim.answerability_breakdown || {};
  const insufficient = breakdown.INSUFFICIENT_EVIDENCE || 0;
  if (insufficient <= 0) return null;
  const total = sim.question_count || 0;
  return {
    id: `ai-visibility:${sim.monitor_id}:${sim.run_id}`, kind: "ai_visibility",
    source: "AI Visibility", type: "insufficient_evidence",
    title: `${insufficient} question${insufficient === 1 ? "" : "s"} have insufficient supporting evidence`,
    problem: `${insufficient} of ${total} tracked question${total === 1 ? "" : "s"} returned `
      + `INSUFFICIENT_EVIDENCE in the most recent Answer Simulator run.`,
    priority: "Medium",
    evidencePreview: [`${insufficient} of ${total} questions checked`],
    fullEvidence: [`${insufficient} of ${total} questions checked`],
    fixText: "Add direct answer sections for the questions with insufficient evidence.",
    destination: { to: "/app/answer-tracking", label: "Review questions" },
    raw: sim,
  };
}

/** `phase4.schema.missing_types[]` / `.entity.missing_signals[]` / `.links.issues[]` ->
 * ActionItem[]. Each already carries its own why/fix text except entity's
 * missing_signals (an array of check keys — see ENTITY_SIGNAL_LABEL), which is
 * aggregated into ONE item, same pattern as `normalizeThinContent`. Every item's
 * `verifySignalId` is the section's own real `related_recommendation_id` field
 * ("schema" for schema/entity, "links" for links — see backend/app/reports/phase4.py),
 * so the existing Fix Verification badge can honestly apply; never invented for a
 * section that has no such field.
 *
 * Dedup: when `recommendationIds` (the set of ids already on this report's
 * `recommendations[]`) contains that SAME `related_recommendation_id`, the whole
 * sub-section is suppressed here — the mature recommendation card (already in this
 * same list) already covers that root cause, so Action Center never shows the same
 * underlying issue as two separate cards. This mirrors ReportView's own
 * SupportingEvidenceNote dedup for the identical fields (see ReportView.jsx),
 * applied here as a Set-membership check on the same real backend id — never fuzzy
 * text matching.
 *
 * `why` is only set where the source genuinely provides a distinct why-it-matters
 * text separate from the problem statement (schema's own `why_it_matters` field) —
 * left unset elsewhere rather than repeating the problem text under a second heading. */
export function normalizePhase4Findings(phase4, scanId, recommendationIds = new Set()) {
  if (!phase4 || phase4.available === false) return [];
  const items = [];
  const covered = (relatedId) => !!relatedId && recommendationIds.has(relatedId);

  if (!covered(phase4.schema?.related_recommendation_id)) {
    for (const m of phase4.schema?.missing_types || []) {
      items.push({
        id: `schema:${m.type}`, kind: "phase4_schema", source: "Schema Intelligence", type: m.type,
        title: `Missing ${m.type} schema`, problem: `Your site is missing ${m.type} schema.`,
        why: m.why_it_matters,
        priority: "Medium",
        evidencePreview: (m.affected_urls || []).slice(0, 3),
        fullEvidence: m.affected_urls || [],
        fixText: m.recommended_action,
        destination: { ...reportDest(scanId, "rep-schema"), label: "Review schema" },
        verifySignalId: phase4.schema?.related_recommendation_id || null,
        raw: m,
      });
    }
  }

  const missingSignals = phase4.entity?.missing_signals || [];
  if (!covered(phase4.entity?.related_recommendation_id) && missingSignals.length > 0) {
    items.push({
      id: `entity:${scanId}`, kind: "phase4_entity", source: "Entity Intelligence",
      type: "missing_entity_signals",
      title: `${missingSignals.length} entity signal${missingSignals.length === 1 ? "" : "s"} missing`,
      problem: missingSignals.map((k) => ENTITY_SIGNAL_LABEL[k] || k).join("; "),
      priority: "Medium",
      evidencePreview: (phase4.entity?.affected_urls || []).slice(0, 3),
      fullEvidence: phase4.entity?.affected_urls || [],
      fixText: "Add the missing entity fields to your Organization/WebSite JSON-LD.",
      destination: { ...reportDest(scanId, "rep-entity"), label: "Review entity" },
      verifySignalId: phase4.entity?.related_recommendation_id || null,
      raw: phase4.entity,
    });
  }

  if (!covered(phase4.links?.related_recommendation_id)) {
    for (const iss of phase4.links?.issues || []) {
      items.push({
        id: `links:${iss.type}`, kind: "phase4_links", source: "Internal Linking", type: iss.type,
        title: iss.label, problem: iss.detail,
        priority: "Low",
        evidencePreview: (iss.affected_urls || []).slice(0, 3),
        fullEvidence: iss.affected_urls || [],
        fixText: iss.recommended_action,
        destination: { ...reportDest(scanId, "rep-links-intel"), label: "Review internal links" },
        verifySignalId: phase4.links?.related_recommendation_id || null,
        raw: iss,
      });
    }
  }

  return items;
}

/** `phase4.questions.questions[]` -> at most ONE aggregate ActionItem, and only when
 * a real gap exists: a question actually found on the site (from FAQ schema or a
 * page heading — see backend/app/reports/phase4.py's build_question_mining) with no
 * `answer` captured alongside it. Never fabricated — `text`/`affected_url` are the
 * real per-item fields, `answer` is real too (str or null), so "no answer captured"
 * is computed, not invented. No severity/why_it_matters/recommended_action field
 * exists on this backend object (unlike schema/links), so this is deliberately kept
 * Low priority and evidence-only — never dressed up as a confirmed defect, and never
 * shown at all when every found question already has a captured answer. No
 * `related_recommendation_id` exists for questions either (confirmed against
 * backend/app/reports/phase4.py) — `verifySignalId` is correspondingly never set. */
export function normalizeQuestionFindings(phase4, scanId) {
  if (!phase4 || phase4.available === false) return null;
  const all = phase4.questions?.questions || [];
  const unanswered = all.filter((q) => !q.answer);
  if (unanswered.length === 0) return null;
  const n = unanswered.length;
  const evidenceLine = (q) => `"${q.text}" — ${q.affected_url}`;
  return {
    id: `questions:${scanId}`, kind: "phase4_questions", source: "Question Opportunities",
    type: "unanswered_question",
    title: `${n} question${n === 1 ? "" : "s"} found with no direct answer captured`,
    problem: `${n} real question${n === 1 ? "" : "s"} were found on your site (from FAQ schema `
      + `or page headings) with no direct answer captured during this scan.`,
    priority: "Low",
    evidencePreview: unanswered.slice(0, 3).map(evidenceLine),
    fullEvidence: unanswered.map(evidenceLine),
    fixText: "Add a direct, concise answer near each question so AI assistants and search engines can extract it.",
    destination: { ...reportDest(scanId, "rep-questions"), label: "Review questions" },
    raw: unanswered,
  };
}

/** `crawl_graph.issues[]` -> ActionItem[]. No `related_recommendation_id` exists on
 * crawl_graph (confirmed against backend/app/reports/crawl_graph.py) — `verifySignalId`
 * is left unset so the Fix Verification badge is honestly never offered for these,
 * rather than guessing at a signal it can't confirm. */
export function normalizeCrawlGraphIssues(crawlGraph, scanId) {
  if (!crawlGraph || crawlGraph.available === false) return [];
  return (crawlGraph.issues || []).map((iss) => ({
    id: `crawl:${iss.code}`, kind: "crawl_graph_issue", source: "Crawl Graph", type: iss.code,
    title: CRAWL_ISSUE_TITLE[iss.code] || iss.label, problem: iss.label,
    priority: iss.severity,
    evidencePreview: (iss.affected_urls || []).slice(0, 3),
    fullEvidence: iss.affected_urls || [],
    fixText: CRAWL_ISSUE_FIX[iss.code] || null,
    destination: { ...reportDest(scanId, "rep-crawl-graph"), label: "Review crawl graph" },
    raw: iss,
  }));
}

/** Sums the locked-item counts for every source Action Center actually normalizes
 * into a card — never a source it doesn't render, so this banner never promises more
 * than opening the report would reveal. Each field is read straight from the
 * server's own gate_X() output (technical_seo.py/content_intelligence.py/phase4.py/
 * crawl_graph.py/insights.py) — never recomputed, guessed, or reconstructed
 * client-side. */
export function computeLockedActionCount(report) {
  if (!report) return 0;
  return (report.locked_recommendation_count || 0)
    + (report.technical_seo?.locked_issue_count || 0)
    + (report.content_intelligence?.locked_cluster_count || 0)
    + (report.phase4?.schema?.locked_missing_count || 0)
    + (report.phase4?.entity?.locked_missing_signal_count || 0)
    + (report.phase4?.links?.locked_issue_count || 0)
    + (report.crawl_graph?.locked_issue_count || 0);
}

function formatEvidenceValue(v) {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (v == null) return "-";
  if (Array.isArray(v)) return v.length ? `${v.length}: ${v.slice(0, 3).join(", ")}${v.length > 3 ? "…" : ""}` : "none";
  if (typeof v === "object") return Object.entries(v).map(([k, val]) => `${k}=${val}`).join(", ");
  return String(v);
}

/** The one orchestration entry point: existing intelligence -> normalize ->
 * deduplicate -> prioritize. Order: priority (Critical>High>Medium>Low), then
 * source (Recommendations > Technical SEO > AI Visibility > Content Intelligence,
 * per the ticket's documented tie-breaker), then id, for full determinism. */
export function buildActionItems({
  recommendations, technicalSeo, contentIntelligence, answerSimulation, phase4, crawlGraph, scanId,
}) {
  const recs = recommendations || [];
  const recommendationIds = new Set(recs.map((r) => r.id));
  const items = [];

  for (const r of recs) items.push(normalizeRecommendation(r, scanId));

  if (technicalSeo?.available) {
    items.push(...normalizeTechnicalIssues(technicalSeo.issues, scanId, recommendationIds));
  }

  const ai = normalizeAnswerSimulation(answerSimulation, scanId);
  if (ai) items.push(ai);

  if (contentIntelligence?.available) {
    for (const c of contentIntelligence.clusters || []) {
      const item = normalizeContentCluster(c, scanId);
      if (item) items.push(item);
    }
    const thin = normalizeThinContent(contentIntelligence.thin_pages, scanId);
    if (thin) items.push(thin);
  }

  items.push(...normalizePhase4Findings(phase4, scanId, recommendationIds));
  items.push(...normalizeCrawlGraphIssues(crawlGraph, scanId));
  const questions = normalizeQuestionFindings(phase4, scanId);
  if (questions) items.push(questions);

  items.sort((a, b) => {
    const pr = (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9);
    if (pr !== 0) return pr;
    const sr = (SOURCE_ORDER[a.source] ?? 9) - (SOURCE_ORDER[b.source] ?? 9);
    if (sr !== 0) return sr;
    return String(a.id).localeCompare(String(b.id));
  });
  return items;
}
