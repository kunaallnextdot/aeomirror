/* Action Center — presentation layer over EXISTING backend intelligence (report.
   recommendations/technical_seo/content_intelligence + the read-only answer-simulation
   summary), never a new audit engine. api.js mocked at the boundary. */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  getReport: vi.fn(),
  getReportAnswerSimulation: vi.fn(async () => ({ available: false, reason: "no_monitor" })),
  getVerifications: vi.fn(async () => ({ verifications: [] })),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));

import { getReport, getReportAnswerSimulation } from "../api.js";
import ActionCenter from "./ActionCenter.jsx";

const REC = (id, overrides = {}) => ({
  id, issue_title: `Fix ${id}`, category: "AI Extractability", severity: "High",
  priority: "High", priority_score: 2, score: 40, weight: 10, status: "fail",
  description: `${id} needs attention.`,
  business_impact: "Reduces trust.", ai_visibility_impact: "Lower answer confidence.",
  estimated_fix_time: "30m", difficulty: "Easy",
  evidence: { findings: { detected: "no" } },
  fix_template: {
    problem: `${id} problem.`, explanation: `${id} explanation.`,
    recommended_fix: [`Fix step for ${id}.`],
    implementation_example: `Example for ${id}.`, expected_outcome: `Outcome for ${id}.`,
  },
  ...overrides,
});

const REPORT = (recommendations, extra = {}) => ({
  recommendations, recommendation_count: recommendations.length,
  locked_recommendation_count: 0, ai: null, ...extra,
});

function renderAC(scanId = "s1") {
  return render(<MemoryRouter><ActionCenter scanId={scanId} /></MemoryRouter>);
}

describe("ActionCenter", () => {
  it("renders the headline and issue count from real data", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema", { priority: "Critical" }), REC("links", { priority: "High" })]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Your latest scan found 2 problems to fix.")).toBeTruthy());
  });

  it("empty state: no fabricated issues when the report has none", async () => {
    getReport.mockResolvedValue(REPORT([]));
    renderAC();
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
    expect(screen.getByText("No high-priority issues were detected in your latest scan.")).toBeTruthy();
    expect(screen.queryByText(/Needs attention/)).toBeNull();
  });

  it("no report yet: honest empty state, not a broken route", async () => {
    const callsBefore = getReport.mock.calls.length;   // other tests in this file also call it
    render(<MemoryRouter><ActionCenter scanId={null} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("No report yet")).toBeTruthy());
    expect(getReport.mock.calls.length).toBe(callsBefore);
  });

  it("priority counts are derived from the actual recommendation set, not invented", async () => {
    getReport.mockResolvedValue(REPORT([
      REC("a", { priority: "Critical" }), REC("b", { priority: "High" }),
      REC("c", { priority: "High" }), REC("d", { priority: "Medium" }),
    ]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Your latest scan found 4 problems to fix.")).toBeTruthy());
    const tiles = Object.fromEntries(Array.from(document.querySelectorAll(".au-ac-count"))
      .map((el) => [el.querySelector(".au-ac-count-l").textContent, el.querySelector(".au-ac-count-n").textContent]));
    expect(tiles).toEqual({ Critical: "1", High: "2", Medium: "1" });
    expect(tiles.Low).toBeUndefined();   // zero-count priorities are hidden, not shown as "0"
  });

  it("cards render in the order the (already priority-sorted) server payload provides", async () => {
    getReport.mockResolvedValue(REPORT([
      REC("first", { priority: "Critical", issue_title: "Fix first" }),
      REC("second", { priority: "Low", issue_title: "Fix second" }),
    ]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix first")).toBeTruthy());
    const titles = Array.from(document.querySelectorAll(".au-ac-card-t")).map((n) => n.textContent);
    expect(titles).toEqual(["Fix first", "Fix second"]);
  });

  it("action card shows a compact preview: problem, real evidence, and a fix summary", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema", {
      description: "Organization schema is missing.",
      evidence: { findings: { organization_entity: "not_detected" } },
      fix_template: { recommended_fix: ["Add Organization JSON-LD."] },
    })]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Organization schema is missing.")).toBeTruthy());
    expect(screen.getByText(/organization_entity: not_detected/)).toBeTruthy();
    expect(screen.getByText("Fix: Add Organization JSON-LD.")).toBeTruthy();
  });

  it("'See fix' reuses the existing RecommendationCard (problem/why/fix/implementation/outcome) — never a duplicated renderer", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    fireEvent.click(screen.getByText("See fix"));
    expect(screen.getByText("schema explanation.")).toBeTruthy();
    expect(screen.getByText("Fix step for schema.")).toBeTruthy();
    expect(screen.getByText("Example for schema.")).toBeTruthy();
    expect(screen.getByText("Outcome for schema.")).toBeTruthy();
  });

  it("deep links use the stable scan id, never an array index or title string", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    renderAC("scan-42");
    await waitFor(() => expect(screen.getByText(/Open report/)).toBeTruthy());
    const link = screen.getByText(/Open report/).closest("a");
    expect(link.getAttribute("href")).toBe("/app/scans/scan-42");
  });

  it("Free/Pro: a locked-count banner appears only when the report reports locked recommendations, never a fake count", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")], { locked_recommendation_count: 3 }));
    renderAC();
    await waitFor(() => expect(screen.getByText(/3 more problems found/)).toBeTruthy());
  });

  it("no locked banner when nothing is locked", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    expect(screen.queryByText(/more problems found/)).toBeNull();
  });

  it("never fabricates an 'In progress' or verification status — none exists in V1", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    expect(screen.queryByText(/In progress/)).toBeNull();
    expect(screen.queryByText(/Verified/)).toBeNull();
    expect(screen.queryByText(/Recently verified/)).toBeNull();
    expect(screen.queryByText(/Partially improved/)).toBeNull();
  });

  it("preview limit: shows at most 6 by default with a 'View all N' expander, never silently dropping critical items", async () => {
    const recs = Array.from({ length: 9 }, (_, i) => REC(`r${i}`, { issue_title: `Fix r${i}`, priority: i === 0 ? "Critical" : "Low" }));
    getReport.mockResolvedValue(REPORT(recs));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix r0")).toBeTruthy());   // critical item always visible
    expect(document.querySelectorAll(".au-ac-card").length).toBe(6);
    fireEvent.click(screen.getByText("View all 9 actions"));
    expect(document.querySelectorAll(".au-ac-card").length).toBe(9);
  });

  it("accessibility: card toggle and 'View all' expose aria-expanded / are keyboard-operable buttons", async () => {
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    const head = screen.getByText("Fix schema").closest(".au-ac-card-h");
    expect(head.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(head);
    expect(head.getAttribute("aria-expanded")).toBe("true");
  });

  it("no N+1 requests: exactly one report fetch regardless of how many cards are expanded", async () => {
    const callsBefore = getReport.mock.calls.length;   // other tests in this file also call it
    getReport.mockResolvedValue(REPORT([REC("a"), REC("b"), REC("c")]));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix a")).toBeTruthy());
    fireEvent.click(screen.getAllByText("See fix")[0]);   // expand card a
    fireEvent.click(screen.getAllByText("See fix")[0]);   // expand card b (a's button is now gone)
    expect(getReport.mock.calls.length - callsBefore).toBe(1);
  });

  it("an honest error is shown on a real fetch failure, never a fabricated empty-state", async () => {
    const { ScanError } = await import("../api.js");
    getReport.mockRejectedValue(new ScanError("Could not load your action items."));
    renderAC();
    await waitFor(() => expect(screen.getByText("Could not load your action items.")).toBeTruthy());
  });
});

describe("ActionCenter V2 — AI Visibility / Content Intelligence / Technical SEO", () => {
  it("a genuine AI Visibility gap (real insufficient-evidence count) appears as an action", async () => {
    getReport.mockResolvedValue(REPORT([]));
    getReportAnswerSimulation.mockResolvedValue({
      available: true, monitor_id: "m1", run_id: "r1", question_count: 8,
      answerability_breakdown: { HIGH: 3, MEDIUM: 1, LOW: 0, INSUFFICIENT_EVIDENCE: 4 },
    });
    renderAC();
    await waitFor(() => expect(screen.getByText("4 questions have insufficient supporting evidence")).toBeTruthy());
    expect(screen.getByText("AI Visibility")).toBeTruthy();
  });

  it("a healthy Answer Simulator run (zero insufficient-evidence) creates no fake action", async () => {
    getReport.mockResolvedValue(REPORT([]));
    getReportAnswerSimulation.mockResolvedValue({
      available: true, monitor_id: "m1", run_id: "r1", question_count: 8,
      answerability_breakdown: { HIGH: 8, MEDIUM: 0, LOW: 0, INSUFFICIENT_EVIDENCE: 0 },
    });
    renderAC();
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
    expect(screen.queryByText(/insufficient/)).toBeNull();
  });

  it("a genuine Technical SEO issue (real affected URLs) appears as an action, deep-links to the report", async () => {
    getReport.mockResolvedValue(REPORT([], {
      technical_seo: { available: true, issues: [
        { code: "NOINDEX_META", severity: "High", count: 3, label: "3 URLs use meta robots noindex", affected_urls: ["/services", "/about", "/contact"] },
      ] },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("3 URLs use meta robots noindex")).toBeTruthy());
    expect(screen.getByText("Technical SEO")).toBeTruthy();
    const link = screen.getByText(/View affected pages/).closest("a");
    // Deep-links to the real Technical SEO section anchor, not just the report's top.
    expect(link.getAttribute("href")).toBe("/app/report?scan=s1#rep-technical-seo");
  });

  it("a genuine Content Intelligence cannibalization cluster appears as an action with real evidence", async () => {
    getReport.mockResolvedValue(REPORT([], {
      content_intelligence: {
        available: true, thin_pages: [],
        clusters: [{
          id: "cluster:a-b", type: "potential_cannibalization", label: "Potential cannibalization",
          severity: "High", recommended_action: "DIFFERENTIATE",
          recommendation: "Review whether these pages should target distinct search intents.",
          evidence: ["Content similarity: 78% (word-trigram overlap)"],
          pages: [{ url: "https://x.com/a" }, { url: "https://x.com/b" }],
        }],
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Potential cannibalization")).toBeTruthy());
    expect(screen.getByText("Content Intelligence")).toBeTruthy();
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("Potential cannibalization").closest(".au-ac-card");
    // the real evidence line appears both as the problem statement (Phase H fix —
    // never the fix-flavored recommendation text) and in the full evidence list
    expect(within(card).getAllByText(/78% \(word-trigram overlap\)/).length).toBeGreaterThanOrEqual(2);
  });

  it("a KEEP_SEPARATE cluster never renders as an action card", async () => {
    getReport.mockResolvedValue(REPORT([], {
      content_intelligence: {
        available: true, thin_pages: [],
        clusters: [{
          id: "cluster:a-b", type: "content_overlap", label: "Content overlap", severity: "Medium",
          recommended_action: "KEEP_SEPARATE", recommendation: "Evidence suggests these pages serve distinct purposes.",
          evidence: [], pages: [{ url: "https://x.com/a" }, { url: "https://x.com/b" }],
        }],
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
  });

  it("Phase I: a cluster with no real evidence shows no empty problem line, and cautious why-it-matters wording distinguishes near_duplicate from potential_cannibalization", async () => {
    getReport.mockResolvedValue(REPORT([], {
      content_intelligence: {
        available: true, thin_pages: [],
        clusters: [{
          id: "cluster:a-b", type: "near_duplicate", label: "Near-duplicate content",
          severity: "High", recommended_action: "CONSOLIDATE",
          recommendation: "Consider consolidating these near-duplicate pages.",
          evidence: [], pages: [{ url: "https://x.com/a" }, { url: "https://x.com/b" }],
        }],
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Near-duplicate content")).toBeTruthy());
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("Near-duplicate content").closest(".au-ac-card");
    expect(within(card).queryByText("", { selector: ".au-ac-problem" })).toBeNull();
    expect(within(card).getByText(/Strong signal/)).toBeTruthy();
  });

  it("mixed sources render together in one list, sorted by priority", async () => {
    getReport.mockResolvedValue(REPORT([REC("content", { priority: "Medium" })], {
      technical_seo: { available: true, issues: [
        { code: "ERROR_5XX", severity: "Critical", count: 1, label: "1 URL returns 5xx", affected_urls: ["/e"] },
      ] },
    }));
    getReportAnswerSimulation.mockResolvedValue({
      available: true, monitor_id: "m1", run_id: "r1", question_count: 5,
      answerability_breakdown: { HIGH: 3, MEDIUM: 0, LOW: 0, INSUFFICIENT_EVIDENCE: 2 },
    });
    renderAC();
    await waitFor(() => expect(screen.getByText("1 URL returns 5xx")).toBeTruthy());
    const titles = Array.from(document.querySelectorAll(".au-ac-card-t")).map((n) => n.textContent);
    // Critical (Technical SEO) first, then the two Medium items (Recommendations
    // before AI Visibility per the documented source tie-break)
    expect(titles).toEqual(["1 URL returns 5xx", "Fix content", "2 questions have insufficient supporting evidence"]);
  });

  it("still exactly 3 requests total (report + answer-simulation + verifications), never one per action", async () => {
    const { getVerifications } = await import("../api.js");
    const reportCallsBefore = getReport.mock.calls.length;
    const simCallsBefore = getReportAnswerSimulation.mock.calls.length;
    const verifyCallsBefore = getVerifications.mock.calls.length;
    getReport.mockResolvedValue(REPORT([REC("a"), REC("b")], {
      technical_seo: { available: true, issues: [
        { code: "ERROR_5XX", severity: "Critical", count: 1, label: "1 URL returns 5xx", affected_urls: ["/e"] },
      ] },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix a")).toBeTruthy());
    fireEvent.click(screen.getAllByText("See fix")[0]);
    fireEvent.click(screen.getAllByText("See fix")[0]);
    expect(getReport.mock.calls.length - reportCallsBefore).toBe(1);
    expect(getReportAnswerSimulation.mock.calls.length - simCallsBefore).toBe(1);
    expect(getVerifications.mock.calls.length - verifyCallsBefore).toBe(1);
  });

  it("Phase K: a genuinely verified-fixed action moves into the collapsed 'Verified fixed' section, not the active list, but still shows its real badge on demand", async () => {
    const { getVerifications } = await import("../api.js");
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    getVerifications.mockResolvedValue({ verifications: [{
      id: "v1", signal_id: "schema", verification_status: "verified",
      status_before: "fail", status_after: "pass", score_before: 40, score_after: 90,
      score_delta: 50, resolved_issues: [], remaining_issues: [], new_issues: [],
      evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
    }] });
    renderAC();
    // Nothing left to fix — the only finding is verified — so the primary list is empty.
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
    expect(screen.queryByText("Needs attention")).toBeNull();
    // The finding itself is never deleted — it's demoted to a collapsed section.
    expect(screen.queryByText("Fix schema")).toBeNull();
    fireEvent.click(screen.getByText("1 verified fixed"));
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    const head = screen.getByText("Fix schema").closest(".au-ac-card-h");
    expect(within(head).getByText("Verified")).toBeTruthy();
  });

  it("no verification badge when no persisted record exists for this action", async () => {
    const { getVerifications } = await import("../api.js");
    getReport.mockResolvedValue(REPORT([REC("schema")]));
    getVerifications.mockResolvedValue({ verifications: [] });   // override the prior test's mock
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    const head = screen.getByText("Fix schema").closest(".au-ac-card-h");
    expect(within(head).queryByText("Verified")).toBeNull();
  });

  describe("Phase K — verified items no longer behave like active unresolved actions", () => {
    it("1. an unresolved (unverified) item remains in the active/primary list", async () => {
      const { getVerifications } = await import("../api.js");
      getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
      getReport.mockResolvedValue(REPORT([REC("schema"), REC("links")]));
      getVerifications.mockResolvedValue({ verifications: [{
        id: "v1", signal_id: "schema", verification_status: "verified",
        status_before: "fail", status_after: "pass", score_before: 40, score_after: 90,
        score_delta: 50, resolved_issues: [], remaining_issues: [], new_issues: [],
        evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
      }] });
      renderAC();
      // "links" has no verification record at all -> stays active; "schema" is verified -> demoted.
      await waitFor(() => expect(screen.getByText("Your latest scan found 1 problem to fix.")).toBeTruthy());
      expect(screen.getByText("Fix links")).toBeTruthy();
      expect(screen.queryByText("Fix schema")).toBeNull();
    });

    it("2. a verified item is excluded from the active count/headline and the priority breakdown", async () => {
      const { getVerifications } = await import("../api.js");
      getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
      getReport.mockResolvedValue(REPORT([REC("schema", { priority: "Critical" })]));
      getVerifications.mockResolvedValue({ verifications: [{
        id: "v1", signal_id: "schema", verification_status: "verified",
        status_before: "fail", status_after: "pass", score_before: 40, score_after: 90,
        score_delta: 50, resolved_issues: [], remaining_issues: [], new_issues: [],
        evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
      }] });
      renderAC();
      await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
      // never counted in the Critical/High/etc. priority tally either
      expect(screen.queryByText("Critical")).toBeNull();
    });

    it("3. the badge/status shown for a verified item stays the real persisted status — never upgraded/invented", async () => {
      const { getVerifications } = await import("../api.js");
      getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
      getReport.mockResolvedValue(REPORT([REC("schema")]));
      getVerifications.mockResolvedValue({ verifications: [{
        id: "v1", signal_id: "schema", verification_status: "partially_improved",
        status_before: "fail", status_after: "warn", score_before: 40, score_after: 70,
        score_delta: 30, resolved_issues: [], remaining_issues: [], new_issues: [],
        evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
      }] });
      renderAC();
      // "partially_improved" is not "verified" -> stays an ACTIVE item, badge says so truthfully.
      await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
      expect(screen.getByText("Your latest scan found 1 problem to fix.")).toBeTruthy();
      const head = screen.getByText("Fix schema").closest(".au-ac-card-h");
      expect(within(head).getByText("Partially improved")).toBeTruthy();
      expect(within(head).queryByText("Verified")).toBeNull();
    });

    it("4. no verification record at all leaves behavior unchanged — item stays active, no verified section renders", async () => {
      const { getVerifications } = await import("../api.js");
      getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
      getReport.mockResolvedValue(REPORT([REC("schema")]));
      getVerifications.mockResolvedValue({ verifications: [] });
      renderAC();
      await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
      expect(screen.getByText("Your latest scan found 1 problem to fix.")).toBeTruthy();
      expect(screen.queryByText(/verified fixed/)).toBeNull();
    });
  });
});

describe("ActionCenter V3 — Schema / Entity / Internal Linking / Crawl Graph", () => {
  it("a genuine missing-schema-type finding appears as an action with its real why_it_matters text on expand", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: { available: true, schema: {
        related_recommendation_id: "schema",
        missing_types: [{
          type: "FAQPage", why_it_matters: "FAQPage schema maps your content directly onto how people ask AI assistants questions.",
          recommended_action: "Add FAQPage JSON-LD with real Question/Answer pairs.",
          affected_urls: ["https://x.com/faq"],
        }],
      }, entity: { missing_signals: [] }, links: { issues: [] } },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Missing FAQPage schema")).toBeTruthy());
    expect(screen.getByText("Schema Intelligence")).toBeTruthy();
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("Missing FAQPage schema").closest(".au-ac-card");
    expect(within(card).getByText("Why it matters")).toBeTruthy();
    expect(within(card).getByText(/maps your content directly onto how people ask/)).toBeTruthy();
    const link = within(card).getByText(/Review schema/).closest("a");
    // Deep-links to the real Schema Intelligence section anchor, not just the report's top.
    expect(link.getAttribute("href")).toBe("/app/report?scan=s1#rep-schema");
  });

  it("missing entity signals are aggregated into ONE action, never one card per signal", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: {
        available: true, schema: { missing_types: [] },
        entity: {
          related_recommendation_id: "schema",
          missing_signals: ["has_sameas", "has_logo"],
          affected_urls: ["https://x.com/"],
        },
        links: { issues: [] },
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("2 entity signals missing")).toBeTruthy());
    expect(screen.getByText("Entity Intelligence")).toBeTruthy();
    expect(document.querySelectorAll(".au-ac-card-t").length).toBe(1);
  });

  it("a genuine internal-linking issue from phase4.links appears as an action", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: {
        available: true, schema: { missing_types: [] }, entity: { missing_signals: [] },
        links: {
          related_recommendation_id: "links",
          issues: [{
            type: "potentially_isolated_page", label: "Potentially isolated page",
            detail: "This page contains no internal links of its own.",
            affected_urls: ["https://x.com/orphan"],
            recommended_action: "Add contextual internal links from this page to related pages.",
          }],
        },
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Potentially isolated page")).toBeTruthy());
    expect(screen.getByText("Internal Linking")).toBeTruthy();
  });

  it("a genuine crawl-graph issue appears as an action and never offers Verify (no related_recommendation_id exists for crawl graph)", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      crawl_graph: {
        available: true,
        issues: [{ code: "ORPHAN_PAGE", severity: "High", count: 2,
          label: "2 crawled pages have zero inbound internal links",
          affected_urls: ["https://x.com/a", "https://x.com/b"] }],
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Orphan pages detected")).toBeTruthy());
    expect(screen.getByText("Crawl Graph")).toBeTruthy();
    expect(screen.getByText("2 crawled pages have zero inbound internal links")).toBeTruthy();
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("Orphan pages detected").closest(".au-ac-card");
    expect(within(card).queryByText(/Verify after re-scan/)).toBeNull();
  });

  it("unavailable phase4/crawl_graph (e.g. single-page or incomplete scan) never fabricates an action", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: { available: false, reason: "scan_incomplete" },
      crawl_graph: { available: false, reason: "multi_page_crawl_required" },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
  });

  it("a schema-derived action shows the Verify badge/link, keyed by the section's own related_recommendation_id", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getReport.mockResolvedValue(REPORT([], {
      phase4: { available: true, schema: {
        related_recommendation_id: "schema",
        missing_types: [{ type: "FAQPage", why_it_matters: "Why.", recommended_action: "Do it.", affected_urls: [] }],
      }, entity: { missing_signals: [] }, links: { issues: [] } },
    }));
    getVerifications.mockResolvedValue({ verifications: [{
      id: "v1", signal_id: "schema", verification_status: "partially_improved",
      status_before: "fail", status_after: "warn", score_before: 40, score_after: 70,
      score_delta: 30, resolved_issues: [], remaining_issues: [], new_issues: [],
      evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
    }] });
    renderAC();
    await waitFor(() => expect(screen.getByText("Missing FAQPage schema")).toBeTruthy());
    const head = screen.getByText("Missing FAQPage schema").closest(".au-ac-card-h");
    expect(within(head).getByText("Partially improved")).toBeTruthy();
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("Missing FAQPage schema").closest(".au-ac-card");
    expect(within(card).getByText(/Verify after re-scan/)).toBeTruthy();
  });

  it("mixes phase4/crawl_graph findings with recommendations/technical_seo in one sorted list, still exactly 3 requests total", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    const reportCallsBefore = getReport.mock.calls.length;
    const simCallsBefore = getReportAnswerSimulation.mock.calls.length;
    const verifyCallsBefore = getVerifications.mock.calls.length;
    getReport.mockResolvedValue(REPORT([REC("a", { priority: "Low" })], {
      phase4: { available: true, schema: {
        related_recommendation_id: "schema",
        missing_types: [{ type: "FAQPage", why_it_matters: "Why.", recommended_action: "Do it.", affected_urls: [] }],
      }, entity: { missing_signals: [] }, links: { issues: [] } },
      crawl_graph: { available: true, issues: [{ code: "ORPHAN_PAGE", severity: "Critical", count: 1,
        label: "1 crawled page has zero inbound internal links", affected_urls: ["https://x.com/a"] }] },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Orphan pages detected")).toBeTruthy());
    const titles = Array.from(document.querySelectorAll(".au-ac-card-t")).map((n) => n.textContent);
    expect(titles).toEqual(["Orphan pages detected", "Missing FAQPage schema", "Fix a"]);
    expect(getReport.mock.calls.length - reportCallsBefore).toBe(1);
    expect(getReportAnswerSimulation.mock.calls.length - simCallsBefore).toBe(1);
    expect(getVerifications.mock.calls.length - verifyCallsBefore).toBe(1);
  });

  it("a phase4 schema finding is suppressed when a recommendation with the SAME id already covers it — no duplicate card for one root cause", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([REC("schema")], {
      phase4: { available: true, schema: {
        related_recommendation_id: "schema",
        missing_types: [{ type: "FAQPage", why_it_matters: "Why.", recommended_action: "Do it.", affected_urls: [] }],
      }, entity: { missing_signals: [] }, links: { issues: [] } },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix schema")).toBeTruthy());
    expect(screen.queryByText("Missing FAQPage schema")).toBeNull();
    expect(document.querySelectorAll(".au-ac-card").length).toBe(1);
  });

  it("a real unanswered-question gap from phase4.questions appears as a Low-priority informational action, never offers Verify", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: {
        available: true, schema: { missing_types: [] }, entity: { missing_signals: [] }, links: { issues: [] },
        questions: { questions: [
          { text: "How much does it cost?", answer: null, source: "page_heading", affected_url: "https://x.com/pricing", category: "pricing" },
          { text: "What is your refund policy?", answer: "30-day refund policy.", source: "schema_faq", affected_url: "https://x.com/faq", category: "policy" },
        ], clusters: {}, total_count: 2 },
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("1 question found with no direct answer captured")).toBeTruthy());
    expect(screen.getByText("Question Opportunities")).toBeTruthy();
    fireEvent.click(screen.getByText("See fix"));
    const card = screen.getByText("1 question found with no direct answer captured").closest(".au-ac-card");
    expect(within(card).getByText(/How much does it cost/)).toBeTruthy();
    expect(within(card).queryByText(/Verify after re-scan/)).toBeNull();
  });

  it("no Question Opportunities action when every found question already has a captured answer — never manufactures a gap", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([], {
      phase4: {
        available: true, schema: { missing_types: [] }, entity: { missing_signals: [] }, links: { issues: [] },
        questions: { questions: [
          { text: "What is your refund policy?", answer: "30-day refund policy.", source: "schema_faq", affected_url: "https://x.com/faq", category: "policy" },
        ], clusters: {}, total_count: 1 },
      },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("You're in good shape.")).toBeTruthy());
    expect(screen.queryByText(/no direct answer captured/)).toBeNull();
  });

  it("the locked-count banner aggregates locked issues across every source Action Center renders, not just locked recommendations", async () => {
    getReportAnswerSimulation.mockResolvedValue({ available: false, reason: "no_monitor" });
    const { getVerifications } = await import("../api.js");
    getVerifications.mockResolvedValue({ verifications: [] });
    getReport.mockResolvedValue(REPORT([REC("a")], {
      locked_recommendation_count: 2,
      technical_seo: { available: true, issues: [], locked_issue_count: 3 },
      content_intelligence: { available: true, clusters: [], thin_pages: [], locked_cluster_count: 1 },
    }));
    renderAC();
    await waitFor(() => expect(screen.getByText("Fix a")).toBeTruthy());
    expect(screen.getByText(/6 more problems found/)).toBeTruthy();
  });
});
