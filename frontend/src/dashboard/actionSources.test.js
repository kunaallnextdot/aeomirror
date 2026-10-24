/* Action Center V2 normalization layer — pure function tests. No React, no network. */
import { describe, it, expect } from "vitest";
import {
  normalizeRecommendation, normalizeContentCluster, normalizeThinContent,
  normalizeTechnicalIssues, normalizeAnswerSimulation, normalizeUrlIntelligenceFindings,
  buildActionItems, CONTENT_CLUSTER_TYPE_CAVEAT,
} from "./actionSources.js";

const REC = (id, overrides = {}) => ({
  id, issue_title: `Fix ${id}`, category: "AI Extractability", priority: "High",
  priority_score: 2, score: 40, description: `${id} problem.`,
  evidence: { findings: { detected: "no" } },
  fix_template: { recommended_fix: [`Fix step for ${id}.`] },
  ...overrides,
});

describe("actionSources — normalizeRecommendation", () => {
  it("maps the existing recommendation fields verbatim, source = Recommendations", () => {
    const item = normalizeRecommendation(REC("schema"), "s1");
    expect(item.id).toBe("schema");
    expect(item.source).toBe("Recommendations");
    expect(item.priority).toBe("High");
    expect(item.problem).toBe("schema problem.");
    expect(item.fixText).toBe("Fix step for schema.");
    expect(item.destination).toEqual({ to: "/app/scans/s1", label: "Open report" });
  });
});

describe("actionSources — normalizeContentCluster", () => {
  const CLUSTER = (over = {}) => ({
    id: "cluster:a-b", type: "potential_cannibalization", label: "Potential cannibalization",
    severity: "High", recommended_action: "DIFFERENTIATE",
    recommendation: "Review whether these pages should target distinct search intents.",
    evidence: ["Content similarity: 78% (word-trigram overlap)"],
    pages: [{ url: "https://x.com/a" }, { url: "https://x.com/b" }],
    ...over,
  });

  it("a cannibalization cluster becomes an action with real evidence and the ticket's exact fix copy", () => {
    const item = normalizeContentCluster(CLUSTER(), "s1");
    expect(item.source).toBe("Content Intelligence");
    expect(item.priority).toBe("High");
    expect(item.fullEvidence).toContain("Content similarity: 78% (word-trigram overlap)");
    expect(item.fullEvidence).toContain("https://x.com/a");
    expect(item.fixText).toBe("Differentiate the pages by search intent and content focus.");
  });

  it("Phase H: problem is the real diagnosed evidence, never the same text as the fix/recommendation", () => {
    const item = normalizeContentCluster(CLUSTER(), "s1");
    // problem must NOT equal the fix-flavored recommendation sentence (the bug this fixes)
    expect(item.problem).not.toBe(CLUSTER().recommendation);
    expect(item.problem).not.toBe(item.fixText);
    // problem must be real, available evidence — the cluster's own first evidence line
    expect(item.problem).toBe("Content similarity: 78% (word-trigram overlap)");
    // fix remains a separate, actionable recommendation
    expect(item.fixText).toBe("Differentiate the pages by search intent and content focus.");
  });

  it("Phase H: never invents evidence when a cluster genuinely has none", () => {
    const item = normalizeContentCluster(CLUSTER({ evidence: [] }), "s1");
    expect(item.problem).toBeNull();
  });

  it("CONSOLIDATE and REVIEW_CANONICAL map to the ticket's exact specified fix copy", () => {
    expect(normalizeContentCluster(CLUSTER({ recommended_action: "CONSOLIDATE" }), "s1").fixText)
      .toBe("Consolidate these pages.");
    expect(normalizeContentCluster(CLUSTER({ recommended_action: "REVIEW_CANONICAL" }), "s1").fixText)
      .toBe("Review canonical targeting.");
  });

  it("KEEP_SEPARATE never becomes an urgent action — it is not necessarily a problem", () => {
    expect(normalizeContentCluster(CLUSTER({ recommended_action: "KEEP_SEPARATE" }), "s1")).toBeNull();
  });

  it("Phase I: near_duplicate and potential_cannibalization carry distinct, cautious why-it-matters wording", () => {
    const dup = normalizeContentCluster(CLUSTER({ type: "near_duplicate" }), "s1");
    const cannibal = normalizeContentCluster(CLUSTER({ type: "potential_cannibalization" }), "s1");
    expect(dup.why).toBe(CONTENT_CLUSTER_TYPE_CAVEAT.near_duplicate);
    expect(cannibal.why).toBe(CONTENT_CLUSTER_TYPE_CAVEAT.potential_cannibalization);
    expect(dup.why).not.toBe(cannibal.why);
    // potential_cannibalization must never claim a confirmed conflict — only an inference
    expect(cannibal.why.toLowerCase()).toMatch(/inference|not proof|may compete/);
  });

  it("Phase I: an unknown/unmapped cluster type never invents why-it-matters copy", () => {
    const item = normalizeContentCluster(CLUSTER({ type: "content_overlap" }), "s1");
    expect(item.why).toBeNull();
  });
});

describe("actionSources — normalizeThinContent", () => {
  it("thin pages become ONE aggregate action, never a fabricated word-count target", () => {
    const item = normalizeThinContent(
      [{ url: "https://x.com/a", word_count: 40, site_median_word_count: 400 },
       { url: "https://x.com/b", word_count: 50, site_median_word_count: 400 }],
      "s1");
    expect(item.title).toBe("2 pages have unusually low content depth");
    expect(item.fixText).toBe("Review whether each page provides enough useful information for its intended topic.");
    expect(item.fixText).not.toMatch(/\d+[, ]*words?/);   // never invents a target word count
    expect(item.fullEvidence[0]).toContain("40 words");
  });

  it("no thin pages -> no action", () => {
    expect(normalizeThinContent([], "s1")).toBeNull();
    expect(normalizeThinContent(null, "s1")).toBeNull();
  });
});

describe("actionSources — normalizeTechnicalIssues", () => {
  const ISSUES = [
    { code: "NOINDEX_META", severity: "High", count: 2, label: "2 URLs use meta robots noindex", affected_urls: ["/a", "/b"] },
    { code: "ROBOTS_BLOCKED", severity: "High", count: 1, label: "1 URL blocked by robots.txt", affected_urls: ["/c"] },
    { code: "REDIRECT_CHAIN", severity: "Medium", count: 1, label: "1 URL redirects through multiple hops", affected_urls: ["/d"] },
  ];

  it("noindex, robots-blocked and redirect issues each become an action with real affected-URL evidence", () => {
    const items = normalizeTechnicalIssues(ISSUES, "s1", new Set());
    expect(items.map((i) => i.type)).toEqual(["NOINDEX_META", "ROBOTS_BLOCKED", "REDIRECT_CHAIN"]);
    const noindex = items.find((i) => i.type === "NOINDEX_META");
    expect(noindex.priority).toBe("High");
    expect(noindex.evidencePreview).toEqual(["/a", "/b"]);
    expect(noindex.fixText).toBe("Review the affected indexability directives.");
  });

  it("dedup: an issue already covered by an existing recommendation for the same signal is excluded", () => {
    const items = normalizeTechnicalIssues(ISSUES, "s1", new Set(["robots"]));
    expect(items.map((i) => i.type)).not.toContain("ROBOTS_BLOCKED");
    expect(items.map((i) => i.type)).toContain("NOINDEX_META");   // unrelated issue still shown
  });

  it("never invents a priority — uses the issue's own existing severity", () => {
    const items = normalizeTechnicalIssues(ISSUES, "s1", new Set());
    for (const i of items) expect(["Critical", "High", "Medium", "Low"]).toContain(i.priority);
  });
});

describe("actionSources — normalizeAnswerSimulation (off-topic / INSUFFICIENT_EVIDENCE protection)", () => {
  it("real insufficient-evidence count becomes ONE aggregate action, never individual question text", () => {
    const item = normalizeAnswerSimulation({
      available: true, monitor_id: "m1", run_id: "r1", question_count: 10,
      answerability_breakdown: { HIGH: 4, MEDIUM: 2, LOW: 0, INSUFFICIENT_EVIDENCE: 4 },
    }, "s1");
    expect(item.title).toBe("4 questions have insufficient supporting evidence");
    expect(item.priority).toBe("Medium");
    expect(item.destination).toEqual({ to: "/app/answer-tracking", label: "Review questions" });
    // the normalized item structurally carries no per-question list/text field at
    // all — an off-topic question's wording (e.g. "best chocolate cake recipe") can
    // never leak through as a fabricated content opportunity, since only the
    // aggregate COUNT from the backend summary is ever read.
    expect(item.questions).toBeUndefined();
    expect(item.raw.answerability_breakdown).toEqual({ HIGH: 4, MEDIUM: 2, LOW: 0, INSUFFICIENT_EVIDENCE: 4 });
  });

  it("zero insufficient-evidence questions -> no action (a healthy AI-visibility run is not a problem)", () => {
    expect(normalizeAnswerSimulation({
      available: true, monitor_id: "m1", run_id: "r1", question_count: 10,
      answerability_breakdown: { HIGH: 8, MEDIUM: 2, LOW: 0, INSUFFICIENT_EVIDENCE: 0 },
    }, "s1")).toBeNull();
  });

  it("no run / unavailable -> no action, never fabricated", () => {
    expect(normalizeAnswerSimulation({ available: false, reason: "no_monitor" }, "s1")).toBeNull();
    expect(normalizeAnswerSimulation(null, "s1")).toBeNull();
  });
});

describe("actionSources — buildActionItems (cross-source dedup/priority/counts)", () => {
  it("mixed sources render together, sorted by priority then documented source tie-break", () => {
    const items = buildActionItems({
      recommendations: [REC("content", { priority: "Medium" })],
      technicalSeo: { available: true, issues: [
        { code: "ERROR_5XX", severity: "Critical", count: 1, label: "1 URL returns 5xx", affected_urls: ["/e"] },
      ] },
      contentIntelligence: null, answerSimulation: null, scanId: "s1",
    });
    expect(items.map((i) => i.source)).toEqual(["Technical SEO", "Recommendations"]);   // Critical before Medium
  });

  it("counts reflect unique action items after dedup, not raw findings", () => {
    const items = buildActionItems({
      recommendations: [REC("robots", { priority: "High" })],
      technicalSeo: { available: true, issues: [
        { code: "ROBOTS_BLOCKED", severity: "High", count: 3, label: "3 URLs blocked by robots.txt", affected_urls: ["/a", "/b", "/c"] },
        { code: "ERROR_4XX", severity: "High", count: 1, label: "1 URL returns 4xx", affected_urls: ["/d"] },
      ] },
      contentIntelligence: null, answerSimulation: null, scanId: "s1",
    });
    // 2 raw technical findings + 1 recommendation = 3 raw, but ROBOTS_BLOCKED is the
    // same root cause as the existing "robots" recommendation -> deduplicated to 2.
    expect(items.length).toBe(2);
    expect(items.map((i) => i.id)).toEqual(expect.arrayContaining(["robots", "tech:ERROR_4XX"]));
    expect(items.map((i) => i.id)).not.toContain("tech:ROBOTS_BLOCKED");
  });

  it("no sources available -> empty list, never a fabricated finding", () => {
    expect(buildActionItems({ recommendations: [], technicalSeo: null, contentIntelligence: null, answerSimulation: null, scanId: "s1" })).toEqual([]);
  });
});

describe("actionSources — normalizeUrlIntelligenceFindings", () => {
  const SUMMARY = (overrides = {}) => ({
    available: true, monitor_id: "mon1",
    findings: [{
      id: "f1", url: "https://acme.example/pricing", template_id: "t1", affected_url_count: 3,
      page_type: "pricing", finding_title: "Missing structured data", category: "structured_data",
      why_it_matters: "AI assistants rely on structured data.", recommended_fix: "Add Product schema.",
      priority: "high", confidence: "high", status: "open", verification_status: null,
    }],
    ...overrides,
  });

  it("maps a real finding into an ActionItem linking to Site Intelligence, never the generic report", () => {
    const [item] = normalizeUrlIntelligenceFindings(SUMMARY(), "s1");
    expect(item.source).toBe("Site Crawl Intelligence");
    expect(item.priority).toBe("High");
    expect(item.confidence).toBe("high");
    expect(item.destination).toEqual({ to: "/app/monitoring/mon1/intelligence", label: "Open Site Intelligence" });
    expect(item.why).toMatch(/3 page/);
  });

  it("unavailable (no monitor/no completed crawl) -> empty, never fabricated", () => {
    expect(normalizeUrlIntelligenceFindings({ available: false, reason: "no_monitor" }, "s1")).toEqual([]);
    expect(normalizeUrlIntelligenceFindings(null, "s1")).toEqual([]);
  });

  it("a finding already verified by recrawl is marked `verified`, its own verification concept", () => {
    const summary = SUMMARY();
    summary.findings[0].verification_status = "verified";
    const [item] = normalizeUrlIntelligenceFindings(summary, "s1");
    expect(item.verified).toBe(true);
  });
});

describe("actionSources — buildActionItems includes url_intelligence and flags cross-source overlaps", () => {
  it("url_intelligence findings appear in the unified list", () => {
    const items = buildActionItems({
      recommendations: [], technicalSeo: null, contentIntelligence: null, answerSimulation: null,
      urlIntelligence: { available: true, monitor_id: "mon1", findings: [{
        id: "f1", url: "https://acme.example/a", affected_url_count: 1, priority: "medium",
        confidence: "medium", status: "open", finding_title: "Thin content", category: "other",
      }] },
      scanId: "s1",
    });
    expect(items.map((i) => i.source)).toContain("Site Crawl Intelligence");
  });

  it("two different sources flagging the SAME affected URL are cross-linked, never silently hidden", () => {
    const items = buildActionItems({
      recommendations: [], contentIntelligence: null, answerSimulation: null,
      technicalSeo: { available: true, issues: [
        { code: "ERROR_4XX", severity: "High", affected_urls: ["https://acme.example/a"], label: "1 URL returns 4xx" },
      ] },
      urlIntelligence: { available: true, monitor_id: "mon1", findings: [{
        id: "f1", url: "https://acme.example/a", affected_url_count: 1, priority: "high",
        confidence: "high", status: "open", finding_title: "Thin content", category: "other",
      }] },
      scanId: "s1",
    });
    const tech = items.find((i) => i.id === "tech:ERROR_4XX");
    const urlIntel = items.find((i) => i.kind === "url_intelligence");
    expect(tech.overlapsWith?.map((o) => o.source)).toContain("Site Crawl Intelligence");
    expect(urlIntel.overlapsWith?.map((o) => o.source)).toContain("Technical SEO");
    // Both items are still present (overlap is a flag, never a suppression).
    expect(items.length).toBe(2);
  });
});
