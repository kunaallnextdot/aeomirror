/* Page Intelligence (per-URL view inside the Page Opportunities drawer) — api.js
   mocked at the boundary. Loading / error / not-analysed states, analyse-now, the most
   important problem first, evidence in every section, the peer comparison and its
   sample, recommendation details with Action Center links, AI suggestions side by side
   and labelled, and viewer restrictions. */
import React from "react";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  getPageIntelligence: vi.fn(), analyzePageNow: vi.fn(), generatePageSuggestions: vi.fn(),
  getUrlOptimization: vi.fn(), changeUrlAction: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));

import { getPageIntelligence, analyzePageNow, generatePageSuggestions, getUrlOptimization, changeUrlAction,
  ScanError } from "../api.js";
import PageIntelligence from "./PageIntelligence.jsx";

const REC = {
  code: "low_search_ctr", issue: "Few Google searchers click through", url: "https://acme.example/services/seo",
  section: "performance", kind: "potential", priority: "high", confidence: "medium", effort: "low",
  benefit_category: "search_visibility",
  evidence: ["1,200 Google impressions and 6 clicks (0.5% CTR) vs a site average of 3.1%."],
  why_it_matters: "Shown often, clicked rarely.", recommended_change: "Rewrite the title to state the service and location.",
  example: { label: "Current title", current: "Services", pattern: "[What the page offers] – [location] | [Brand]" },
  dependencies: ["Access to edit title and meta description"],
  verification: "Wait for 200 new impressions, then compare CTR.", action_center_finding: null,
};
const REC2 = { ...REC, code: "missing_meta_description", issue: "Missing meta description", priority: "medium",
  section: "content", evidence: ["Observed on this page: no meta description"],
  action_center_finding: { id: "f1", title: "Missing meta description — 4 service pages", status: "open" } };
const ANALYSIS = {
  summary: { top_issue: REC, recommendation_count: 2, readiness: { met: 2, assessed: 4 } },
  checks: [
    { section: "content", code: "title", status: "pass", title: "Title length", observed: "48 characters" },
    { section: "technical", code: "orphan_page", status: "warn", title: "No crawled page links here", observed: "0 crawled pages link here" },
    { section: "schema", code: "missing_relevant_schema", status: "warn", title: "Structured data doesn't describe this page type", observed: "found WebPage; expected Service" },
    { section: "readiness", code: "questions", status: "not_assessable", title: "Some sections answer a specific question", detail: "Section headings weren't recorded for this crawl." },
    { section: "readiness", code: "citations", status: "info", title: "Cited in tracked AI answers", observed: "1 of 30 tracked answers in the run of 2026-10-30" },
  ],
  patterns: [{ code: "low_search_ctr", text: "1,200 Google impressions and 6 clicks (0.5% CTR) vs a site average of 3.1%.", sample: "1,200 impressions" }],
  comparison: { available: true, basis: "conversion_rate", basis_label: "conversions per session", minimum: "at least 30 sessions",
    qualifying_pages: 12, position: "weak", successful: { n: 4, median_metric: 0.1 }, weak: { n: 4, median_metric: 0 },
    differences: [{ feature: "word_count", text: "Stronger service pages (n=4) have a median of 900 words of text; weaker ones (n=4) 180. This page has 180." }],
    disclaimer: "These are associations in your own data, not proof of cause." },
  recommendations: [REC, REC2],
  readiness_note: "No check can guarantee that ChatGPT, Gemini, Claude or any AI assistant will cite this page.",
  disclaimer: "These are associations in your own data, not proof of cause.",
};
const base = (over = {}) => ({
  status: "current", analysis: ANALYSIS, history: [{ id: "a1", analyzed_at: "2026-11-02T09:00:00", recommendations: 2,
    resolved_since_previous: ["missing_h1"], new_since_previous: [] }],
  verification: [{ finding_id: "f1", title: "Missing meta description — 4 service pages", verification_status: "unchanged" }],
  suggestions: null, ai_suggestions_available: true, ...over,
});
const SUGG = {
  ai_generated: true, notice: "AI-generated suggestions based on this page's crawled content. Review them before use; nothing is changed on your website.",
  pairs: [{ field: "title", original: "Services", suggested: "SEO Audits in Leeds | Acme", rationale: "Names the service." }],
  current_headings: ["Our services"], heading_outline: ["What's included", "Pricing"],
  missing_sections: [{ heading: "Pricing", why: "Not on the page." }],
  faq: [{ question: "How long does an audit take?", answer_outline: "[add: typical duration]" }],
  internal_links: [{ url: "https://acme.example/pricing", anchor_text: "pricing", reason: "Related" }],
  schema: { type: "Service", notes: "Add Service data." }, limitations: null, generated_at: "2026-11-02T10:00:00",
};

const ACT = (over = {}) => ({
  code: "low_search_ctr", issue: "Few Google searchers click through", status: "new", tracked: false,
  verification_method: "performance", last_result: null, next_check_at: null, recheck_requested: false, ...over,
});
const OPT = (over = {}) => ({
  actions: [ACT(), ACT({ code: "missing_meta_description", issue: "Missing meta description", status: "ready_for_verification",
                         tracked: true, verification_method: "page_check", next_check_at: "2026-11-02T10:00:00" })],
  earlier_actions: [], next_action: { code: "low_search_ctr", issue: "Few Google searchers click through",
                                      reasons: ["Business Value Score 72 (high value page)."] },
  held_back: [],
  change: { classification: "improvement", text: "Issues found on the previous crawl are no longer found.",
            changes: [{ field: "title", label: "Title", before: "SEO", after: "SEO audits in Leeds" }],
            new_issues: [], resolved_issues: [{ code: "missing_h1", issue: "No H1 heading" }] },
  timeline: [
    { at: "2026-11-02T10:00:00", kind: "verification", code: "missing_h1", issue: "No H1 heading",
      text: "Verification by page re-check: fixed",
      detail: { method: "recrawl", result: "fixed", before: { heading_counts: { h1: 0 } }, after: { heading_counts: { h1: 1 } },
                before_observed: "Observed on this page: 0 H1 heading(s)" } },
    { at: "2026-11-01T10:00:00", kind: "note", code: "missing_h1", issue: "No H1 heading", text: "Note added", note: "Dev ticket 42" },
  ],
  impact: [{ code: "missing_h1", issue: "No H1 heading", status: "verified", comparison: {
    excluded_period: ["2026-09-01", "2026-09-10"], window_days: 28, disclaimer: "These are changes observed between the two periods, not proof that the fix caused them.",
    sources: [{ source: "ga4", label: "Google Analytics 4", changes: { ai_sessions: { before: 10, after: 20, delta: 10, pct: 100 } },
                before: { status: "complete", window: ["2026-08-04", "2026-08-31"], metrics: { ai_sessions: 10 } },
                after: { status: "complete", window: ["2026-09-11", "2026-10-08"], metrics: { ai_sessions: 20 } } },
              { source: "gsc", label: "Google Search Console", changes: {},
                before: { status: "unavailable", window: [null, null], metrics: null, reason: "Search Console isn't connected." },
                after: { status: "pending", window: [null, null], metrics: null, reason: "Waiting for a Search Console window." } }] } }],
  ...over,
});

function renderPI(props = {}) {
  render(
    <MemoryRouter>
      <PageIntelligence monitorId="m1" crawlUrlId="u1" canRun
                        fallbackAction={{ kind: "page_issue", title: "Missing meta description", action: "Add one.", evidence: "no meta description" }}
                        {...props}>
        {(analysed) => <div data-testid="overview">{analysed ? "analysed" : "not analysed"}</div>}
      </PageIntelligence>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  getPageIntelligence.mockResolvedValue(base());
  getUrlOptimization.mockResolvedValue(OPT());
});

describe("states", () => {
  it("loads, then shows the most important problem first with its evidence", async () => {
    let resolve;
    getPageIntelligence.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderPI();
    expect(screen.queryByText(/most important problem/i)).toBeNull();
    await act(async () => resolve(base()));
    const top = screen.getByText(/most important problem/i).closest(".sp-po-action");
    within(top).getByText("Few Google searchers click through");
    within(top).getByText(/rewrite the title to state the service/i);
    within(top).getByText(/0\.5% CTR/);
    expect(screen.getByTestId("overview").textContent).toBe("analysed");
  });

  it("an error is shown, not a blank panel", async () => {
    getPageIntelligence.mockRejectedValue(new ScanError("Page not found.", 404));
    renderPI();
    await screen.findByText("Page not found.");
  });

  it("before analysis it falls back to the page's checked issue and offers to analyse now", async () => {
    getPageIntelligence.mockResolvedValue(base({ status: "not_analyzed", analysis: null, history: [], verification: [] }));
    analyzePageNow.mockResolvedValue(base());
    renderPI();
    await screen.findByText("Missing meta description");
    expect(screen.getByTestId("overview").textContent).toBe("not analysed");
    fireEvent.click(screen.getByRole("button", { name: /analyse this page now/i }));
    await screen.findByText(/most important problem/i);
    expect(analyzePageNow).toHaveBeenCalledWith("m1", "u1");
  });

  it("a page with no problems says so", async () => {
    getPageIntelligence.mockResolvedValue(base({ analysis: { ...ANALYSIS, summary: { ...ANALYSIS.summary, top_issue: null },
                                                             recommendations: [], patterns: [] } }));
    renderPI();
    await screen.findByText("No problem found");
    await screen.findByText(/nothing to change based on the evidence/i);
  });

  it("a viewer can't analyse or generate suggestions", async () => {
    getPageIntelligence.mockResolvedValue(base({ status: "queued", analysis: null }));
    renderPI({ canRun: false });
    await screen.findByText(/queued for analysis/i);
    expect(screen.queryByRole("button", { name: /analyse this page now/i })).toBeNull();
    fireEvent.click(screen.getByText(/ai content suggestions/i));
    screen.getByText(/ask an admin or member to generate suggestions/i);
  });
});

describe("analysis sections", () => {
  it("each section lists checks with what was observed, and unknowns say so", async () => {
    renderPI();
    const tech = (await screen.findByText(/content and technical · 1 to review/i)).closest("details");
    within(tech).getByText(/0 crawled pages link here/);
    const schema = screen.getByText(/entity and structured data · 1 to review/i).closest("details");
    within(schema).getByText(/expected Service/);
    const ready = screen.getByText(/ai answer readiness · 2 of 4 checks met/i).closest("details");
    within(ready).getByText("Can't tell");
    within(ready).getByText(/1 of 30 tracked answers/);
    within(ready).getByText(/no check can guarantee/i);
  });

  it("the comparison states its basis, sample and that it isn't proof of cause", async () => {
    renderPI();
    const cmp = (await screen.findByText(/compared with similar pages/i)).closest("details");
    within(cmp).getByText(/based on conversions per session across 12 pages/i);
    within(cmp).getByText(/in the weak group/i);
    within(cmp).getByText(/median of 900 words/);
    within(cmp).getByText(/not proof of cause/i);
  });

  it("an unavailable comparison explains why", async () => {
    getPageIntelligence.mockResolvedValue(base({ analysis: { ...ANALYSIS, comparison: { available: false,
      reason: "Only 4 page(s) of this type have at least 30 sessions; at least 9 are needed." } } }));
    renderPI();
    const cmp = (await screen.findByText(/compared with similar pages/i)).closest("details");
    within(cmp).getByText(/only 4 page\(s\)/i);
  });

  it("recommendations carry evidence, change, example, needs, verification and the Action Center link", async () => {
    renderPI();
    const recs = (await screen.findByText(/recommended actions \(2\)/i)).closest("details");
    const card = within(recs).getByText("Missing meta description").closest("details");
    fireEvent.click(within(card).getByText("Missing meta description"));
    within(card).getByText("Observed on this page: no meta description");
    within(card).getByText(/tracked as “missing meta description — 4 service pages”/i);
    const ctr = within(recs).getByText("Few Google searchers click through").closest("details");
    within(ctr).getByText(/medium confidence · low effort · search visibility/i);
    within(ctr).getByText(/wait for 200 new impressions/i);
    within(ctr).getByText("Services");
  });

  it("history shows resolved issues and verification status", async () => {
    renderPI();
    const h = (await screen.findByText(/history and verification/i)).closest("details");
    within(h).getByText(/resolved since the previous analysis: missing h1/i);
    within(h).getByText(/4 service pages — unchanged/);
  });
});

describe("AI suggestions", () => {
  it("are generated on request, labelled, and shown next to the current text", async () => {
    generatePageSuggestions.mockResolvedValue(SUGG);
    renderPI();
    fireEvent.click(await screen.findByText(/ai content suggestions/i));
    fireEvent.click(screen.getByRole("button", { name: /generate suggestions/i }));
    await screen.findByText(/ai-generated suggestions based on this page/i);
    screen.getByText(/nothing is changed on your website/i);
    const row = screen.getByText("SEO Audits in Leeds | Acme").closest("tr");
    within(row).getByText("Services");
    screen.getByRole("columnheader", { name: "AI suggestion" });
    screen.getByText(/\[add: typical duration\]/);
    screen.getByText(/pricing → https:\/\/acme\.example\/pricing/);
  });

  it("a failure is reported and AI-disabled servers say so", async () => {
    generatePageSuggestions.mockRejectedValue(new ScanError("Today's AI suggestion limit has been reached. Try again tomorrow.", 429));
    renderPI();
    fireEvent.click(await screen.findByText(/ai content suggestions/i));
    fireEvent.click(screen.getByRole("button", { name: /generate suggestions/i }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/limit has been reached/));
  });

  it("aren't offered when AI isn't configured", async () => {
    getPageIntelligence.mockResolvedValue(base({ ai_suggestions_available: false }));
    renderPI();
    fireEvent.click(await screen.findByText(/ai content suggestions/i));
    screen.getByText(/aren't configured on this server/i);
  });
});

describe("fix tracking", () => {
  it("each recommendation shows its status and a marked fix is never shown as verified", async () => {
    renderPI();
    const recs = (await screen.findByText(/recommended actions \(2\)/i)).closest("details");
    const card = within(recs).getByText("Missing meta description").closest("details");
    expect((await within(card).findAllByText("Ready for verification")).length).toBe(2);   // header + controls
    within(card).getByText(/counts as verified only after a check/i);
    within(card).getByText(/a page re-check is queued/i);
    expect(within(card).queryByRole("button", { name: /mark fixed/i })).toBeNull();
    within(card).getByRole("button", { name: /cancel re-check/i });
    const perf = within(recs).getByText("Few Google searchers click through").closest("details");
    within(perf).getByText(/a page check can't confirm it/i);
    expect(within(perf).queryByRole("button", { name: /re-check now/i })).toBeNull();
  });

  it("actions call the API and the record refreshes; ignoring needs a reason", async () => {
    changeUrlAction.mockResolvedValue(OPT({ actions: [ACT({ status: "in_progress", tracked: true })] }));
    renderPI();
    const recs = (await screen.findByText(/recommended actions \(2\)/i)).closest("details");
    const perf = within(recs).getByText("Few Google searchers click through").closest("details");
    fireEvent.click(await within(perf).findByRole("button", { name: "Start" }));
    await waitFor(() => expect(within(perf).getAllByText("In progress").length).toBeGreaterThan(0));
    expect(changeUrlAction).toHaveBeenCalledWith("m1", "u1", "low_search_ctr", "start", undefined);
    fireEvent.click(within(perf).getByRole("button", { name: "Ignore" }));
    const submit = within(perf).getAllByRole("button", { name: "Ignore" }).at(-1);
    expect(submit.disabled).toBe(true);
    fireEvent.change(within(perf).getByLabelText(/reason for ignoring/i), { target: { value: "Seasonal page" } });
    fireEvent.click(submit);
    await waitFor(() => expect(changeUrlAction).toHaveBeenLastCalledWith("m1", "u1", "low_search_ctr", "ignore", { reason: "Seasonal page" }));
  });

  it("a failed change is reported", async () => {
    changeUrlAction.mockRejectedValue(new ScanError("This action is verified, so that change isn't possible.", 409));
    renderPI();
    const recs = (await screen.findByText(/recommended actions \(2\)/i)).closest("details");
    const perf = within(recs).getByText("Few Google searchers click through").closest("details");
    fireEvent.click(await within(perf).findByRole("button", { name: "Start" }));
    await waitFor(() => expect(within(perf).getByRole("alert").textContent).toMatch(/isn't possible/));
  });

  it("viewers see status but no controls", async () => {
    renderPI({ canRun: false });
    const recs = (await screen.findByText(/recommended actions \(2\)/i)).closest("details");
    await within(recs).findAllByText("Ready for verification");
    expect(within(recs).queryByRole("button", { name: /start|mark fixed|ignore/i })).toBeNull();
  });

  it("shows the next action, the change since the last crawl, the timeline with evidence and labelled impact", async () => {
    renderPI();
    const sec = (await screen.findByText(/fix tracking and timeline/i)).closest("details");
    within(sec).getByText(/next best action on this page/i);
    within(sec).getByText(/since the previous crawl: improvement/i);
    within(sec).getByText(/title: SEO → SEO audits in Leeds/i);
    within(sec).getByText(/verification by page re-check: fixed/i);
    within(sec).getByText(/“Dev ticket 42”/);
    within(sec).getByText(/Observed on this page: 0 H1/);
    within(sec).getByText("+10 (+100%)");
    within(sec).getByText(/change period\) is left out/i);
    within(sec).getByText(/not proof that the fix caused them/i);
    within(sec).getByText(/Search Console isn't connected/);
    within(sec).getByText("Waiting for data");
  });

  it("a recommendation already verified isn't shown as the most important problem", async () => {
    getUrlOptimization.mockResolvedValue(OPT({ actions: [ACT({ status: "verified", tracked: true }),
      ACT({ code: "missing_meta_description", issue: "Missing meta description", status: "new" })] }));
    renderPI();
    await waitFor(() => {
      const top = screen.getByText(/most important problem/i).closest(".sp-po-action");
      expect(within(top).queryByText("Few Google searchers click through")).toBeNull();
      within(top).getByText("Missing meta description");
    });
  });

  it("a fix-tracking load failure doesn't break the analysis", async () => {
    getUrlOptimization.mockRejectedValue(new ScanError("URL optimization tracking is a Pro feature.", 402));
    renderPI();
    await screen.findByText(/most important problem/i);
    await screen.findByText(/pro feature/i);
  });
});
