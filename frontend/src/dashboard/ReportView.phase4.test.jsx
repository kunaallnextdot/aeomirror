/* Phase 4 — deep AEO intelligence sections in ReportView: Schema/Internal Link/Entity
   Intelligence + Question Opportunities. Server-side gated (report.phase4, see backend
   `gate_phase4`) — free/paid is asserted by what the mocked API RETURNS, matching the
   pattern in ReportView.recommendations.test.jsx: the component renders whatever it's
   given, it never slices/blurs real content client-side. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

const BASE_REPORT = {
  domain: "example.com", url: "https://example.com/", scanned_at: "2026-01-01T00:00:00Z",
  scorecard: {
    overall_score: 62, grade: "C", status: "ok",
    category_scores: [], strengths: [], weaknesses: [], quick_wins: [], top_priorities: [],
    issue_counts: { Critical: 1, High: 0, Medium: 2, Low: 0 }, summary: "Test summary.",
  },
  recommendations: [], recommendation_count: 0, insights: null, ai: null,
};

const FREE_PHASE4 = {
  schema: {
    state: "present", malformed_blocks: 0, detected_types: ["WebSite"],
    present_types: ["WebSite"],
    missing_types: [
      { type: "Organization", why_it_matters: "Hard to identify who you are.",
        recommended_action: "Add Organization JSON-LD.", affected_urls: ["https://example.com/"] },
      { type: "Article", why_it_matters: "Signals authored content.",
        recommended_action: "Add Article JSON-LD.", affected_urls: ["https://example.com/"] },
    ],
    preview: true, locked_missing_count: 5, bulk: null,
    related_recommendation_id: "schema",
  },
  links: {
    internal_links: 0, has_nav: false, anchor_diversity: 0, generic_anchors: 0, empty_anchors: 0,
    issues: [{ type: "potentially_isolated_page", label: "Potentially isolated page",
              detail: "No internal links on this page.", affected_urls: ["https://example.com/"],
              recommended_action: "Add contextual internal links." }],
    preview: true, locked_issue_count: 0,
    inbound_link_graph_note: "Insufficient crawl graph data.", bulk: null,
    related_recommendation_id: "links",
  },
  entity: {
    primary_entity_types: [], primary_entity_name: null, entity_url: null, logo: null,
    same_as: [], same_as_note: "No sameAs relationship detected.",
    missing_signals: ["has_entity_schema", "has_sameas"], completeness_pct: 33.3,
    preview: true, locked_missing_signal_count: 2, locked_same_as_count: 0,
    knowledge_graph_note: "No Google Knowledge Graph / Knowledge Panel evidence is available.",
    related_recommendation_id: "schema",
  },
  questions: {
    questions: [{ text: "What does Acme treat?", answer: "Back pain.", source: "schema_faq",
                 affected_url: "https://example.com/", category: "informational" }],
    preview: true, locked_question_count: 4, total_count: 5,
  },
};

const PAID_PHASE4 = {
  schema: { ...FREE_PHASE4.schema, missing_types: [...FREE_PHASE4.schema.missing_types,
    { type: "FAQPage", why_it_matters: "Maps to how people ask AI.",
      recommended_action: "Add FAQPage JSON-LD.", affected_urls: ["https://example.com/"] }],
    preview: undefined, locked_missing_count: undefined },
  links: { ...FREE_PHASE4.links, preview: undefined, locked_issue_count: undefined },
  entity: { ...FREE_PHASE4.entity, same_as: ["https://linkedin.com/company/acme"],
    same_as_note: null, preview: undefined, locked_missing_signal_count: undefined },
  questions: { ...FREE_PHASE4.questions,
    questions: [...FREE_PHASE4.questions.questions,
      { text: "How much does it cost?", answer: null, source: "page_heading",
        affected_url: "https://example.com/", category: "pricing" }],
    preview: undefined, locked_question_count: undefined },
};

vi.mock("../api.js", () => ({
  getReport: vi.fn(),
  getReportAccess: vi.fn(),
  getContentInsights: vi.fn(async () => ({ insights: [] })),
  getReportAIVisibility: vi.fn(async () => ({ available: false, reason: "no_monitor" })),
  getQuestionBank: vi.fn(async () => ({ available: true, questions: [], total_count: 0 })),
  getVerifications: vi.fn(async () => ({ verifications: [] })),
  downloadReport: vi.fn(),
  ScanError: class ScanError extends Error {},
}));
vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));

import { getReport, getReportAccess } from "../api.js";
import ReportView from "./ReportView.jsx";

describe("ReportView Phase 4 sections", () => {
  it("free: shows a small real preview of each section + locked counts, no full data", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, phase4: FREE_PHASE4 });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Schema Intelligence")).toBeTruthy());
    expect(screen.getByText("Missing Organization schema")).toBeTruthy();
    expect(screen.getByText("Missing Article schema")).toBeTruthy();
    expect(screen.getByText("Hard to identify who you are.")).toBeTruthy();   // real why_it_matters
    expect(screen.getByText("Add Organization JSON-LD.")).toBeTruthy();       // real recommended_action
    expect(screen.getByText(/5 more missing schema type/)).toBeTruthy();

    expect(screen.getByText("Internal Link Intelligence")).toBeTruthy();
    expect(screen.getByText("Potentially isolated page")).toBeTruthy();
    expect(screen.queryByText(/orphan/i)).toBeNull();

    expect(screen.getByText("Entity Intelligence")).toBeTruthy();
    expect(screen.getAllByText(/No sameAs relationship detected/).length).toBeGreaterThan(0);
    expect(screen.getByText("No Organization/entity schema detected")).toBeTruthy();
    expect(screen.getByText(/2 more missing entity signal/)).toBeTruthy();

    expect(screen.getByText("Question Opportunities")).toBeTruthy();
    expect(screen.getByText(/What does Acme treat\?/)).toBeTruthy();
    expect(screen.getByText(/4 more question/)).toBeTruthy();
    // the locked questions/schema types themselves never rendered
    expect(screen.queryByText(/How much does it cost\?/)).toBeNull();
    expect(screen.queryByText("Missing FAQPage schema")).toBeNull();
  });

  it("paid: shows complete data with no lock affordances", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, phase4: PAID_PHASE4 });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Missing FAQPage schema")).toBeTruthy());
    expect(screen.getByText(/How much does it cost\?/)).toBeTruthy();
    expect(screen.getByText(/linkedin\.com\/company\/acme/)).toBeTruthy();
    expect(screen.queryByText(/more missing schema type/)).toBeNull();
    expect(screen.queryByText(/more question/)).toBeNull();
  });

  it("Phase I: Schema and Entity subtitles communicate the content-dependent vs. universal distinction", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, phase4: PAID_PHASE4 });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Schema Intelligence")).toBeTruthy());
    expect(screen.getByText(/some types only apply when your content supports them/)).toBeTruthy();
    expect(screen.getByText(/checked the same way for every site/)).toBeTruthy();
  });

  it("empty state: a section with nothing to report renders nothing (no fabricated content)", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      phase4: {
        schema: { state: "present", malformed_blocks: 0, detected_types: ["Organization", "WebSite", "Article",
          "FAQPage", "BreadcrumbList", "Product", "Service", "Review"],
          present_types: ["Organization", "WebSite", "Article", "FAQPage", "BreadcrumbList", "Product", "Service", "Review"],
          missing_types: [], bulk: null },
        links: { internal_links: 10, has_nav: true, anchor_diversity: 0.8, generic_anchors: 0,
                empty_anchors: 0, issues: [], inbound_link_graph_note: "note", bulk: null },
        entity: { primary_entity_types: ["Organization"], primary_entity_name: "Acme",
                 entity_url: "https://example.com/", logo: "https://example.com/logo.png",
                 same_as: ["https://linkedin.com/company/acme"], same_as_note: null,
                 missing_signals: [], completeness_pct: 100.0,
                 knowledge_graph_note: "note" },
        questions: { questions: [], total_count: 0 },
      },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Entity Intelligence")).toBeTruthy());
    expect(screen.getByText(/No missing schema types found/)).toBeTruthy();
    expect(screen.getByText(/No internal-linking issues found/)).toBeTruthy();
    // no questions -> the whole section is omitted, not shown empty
    expect(screen.queryByText("Question Opportunities")).toBeNull();
  });

  it("incomplete scan: shows an honest 'scan in progress' state, never a fabricated diagnosis", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      phase4: { available: false, reason: "scan_incomplete" },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Deeper AEO Intelligence")).toBeTruthy());
    expect(screen.getByText(/Scan still in progress/)).toBeTruthy();
    // none of the four sections (or any fabricated finding) render
    expect(screen.queryByText("Schema Intelligence")).toBeNull();
    expect(screen.queryByText("Internal Link Intelligence")).toBeNull();
    expect(screen.queryByText("Entity Intelligence")).toBeNull();
    expect(screen.queryByText("Question Opportunities")).toBeNull();
    expect(screen.queryByText(/Missing .* schema/)).toBeNull();
  });

  it("Phase C: Entity healthy state — no missing signals never becomes an invented recommendation", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, phase4: {
      ...FREE_PHASE4,
      entity: { ...FREE_PHASE4.entity, missing_signals: [], same_as: ["https://linkedin.com/company/acme"], same_as_note: null },
    } });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Entity Intelligence")).toBeTruthy());
    expect(screen.getByText("No missing entity signals detected. 🎉")).toBeTruthy();
    expect(screen.queryByText(/entity signal.*missing/)).toBeNull();
    // scoped to the Entity section itself — no diagnosis card (and no "How to fix")
    // is invented there when the underlying data is genuinely healthy.
    const entitySection = document.getElementById("rep-entity");
    expect(entitySection.textContent).not.toMatch(/How to fix/);
  });

  it("Phase C: Entity actionable state — a real missing signal renders one aggregate diagnosis card (not one per signal)", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, phase4: FREE_PHASE4 });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("2 entity signals missing")).toBeTruthy());
    expect(screen.getByText("No Organization/entity schema detected")).toBeTruthy();
    expect(screen.getByText("Add the missing entity fields to your Organization/WebSite JSON-LD.")).toBeTruthy();
    // real knowledge_graph_note stays — never a claim of Knowledge Graph presence
    expect(screen.getByText(/No Google Knowledge Graph/)).toBeTruthy();
  });

  it("Phase C: a schema gap already covered by an existing 'schema' recommendation renders as supporting evidence with a jump-link, never a duplicate diagnosis card", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [{
        id: "schema", issue_title: "Add structured data", category: "Schema",
        severity: "High", priority: "High", priority_score: 5, score: 40, weight: 10, status: "fail",
        description: "Structured data is incomplete.", business_impact: "x", ai_visibility_impact: "y",
        estimated_fix_time: "30m", difficulty: "Easy", fix_template: { recommended_fix: ["Add JSON-LD"] },
      }],
      phase4: FREE_PHASE4,
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getAllByRole("button", { name: /"Add structured data" recommendation/ }).length).toBeGreaterThan(0));
    expect(document.body.textContent).toMatch(/Already covered by/);
    // both schema AND entity share the same "schema" related_recommendation_id, so
    // BOTH become supporting evidence — never two separate duplicate actions on top
    // of the one real recommendation card.
    expect(screen.queryByText("Missing Organization schema")).toBeNull();
    expect(screen.queryByText("2 entity signals missing")).toBeNull();
    // exactly ONE "Verify after re-scan" — the recommendation card's own (real,
    // pre-existing) link — never a second, duplicated one from Schema/Entity.
    expect(screen.getAllByText(/Verify after re-scan/).length).toBe(1);
  });
});
