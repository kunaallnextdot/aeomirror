/* Action Center V2 normalization layer — pure function tests. No React, no network. */
import { describe, it, expect } from "vitest";
import {
  normalizeRecommendation, normalizeContentCluster, normalizeThinContent,
  normalizeTechnicalIssues, normalizeAnswerSimulation, buildActionItems,
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
    expect(item.problem).toBe("Review whether these pages should target distinct search intents.");
    expect(item.fullEvidence).toContain("Content similarity: 78% (word-trigram overlap)");
    expect(item.fullEvidence).toContain("https://x.com/a");
    expect(item.fixText).toBe("Differentiate the pages by search intent and content focus.");
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
