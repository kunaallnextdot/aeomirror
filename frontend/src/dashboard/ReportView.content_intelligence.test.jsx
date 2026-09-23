/* Content Cannibalization & Duplicate Content Intelligence section in ReportView.
   Server-side gated (report.content_intelligence, see backend
   `gate_content_intelligence`) — free/paid is asserted by what the mocked API
   RETURNS, matching the pattern in ReportView.crawl_graph.test.jsx: the component
   renders whatever it's given, it never slices/blurs real content client-side.
   Terminology is asserted to stay strictly "potential" — no claim of confirmed
   Google search-result cannibalization. */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

const BASE_REPORT = {
  domain: "example.com", url: "https://example.com/", scanned_at: "2026-01-01T00:00:00Z",
  scorecard: {
    overall_score: 62, grade: "C", status: "ok",
    category_scores: [], strengths: [], weaknesses: [], quick_wins: [], top_priorities: [],
    issue_counts: { Critical: 1, High: 0, Medium: 2, Low: 0 }, summary: "Test summary.",
  },
  recommendations: [], recommendation_count: 0, insights: null, ai: null,
  phase4: { available: false, reason: "scan_incomplete" },
  technical_seo: { available: false, reason: "scan_incomplete" },
  crawl_graph: { available: false, reason: "scan_incomplete" },
};

const CANNIBAL_CLUSTER = {
  id: "cluster:a-b", type: "potential_cannibalization", label: "Potential cannibalization",
  severity: "High", confidence: "high",
  pages: [
    { url: "https://example.com/seo-services", title: "SEO Services", h1: "SEO Services",
      word_count: 320, canonical: "https://example.com/seo-services" },
    { url: "https://example.com/seo-agency", title: "SEO Agency", h1: "SEO Agency",
      word_count: 290, canonical: "https://example.com/seo-agency" },
  ],
  evidence: ["Content similarity: 12% (word-trigram overlap)", "Similar titles",
            "Pages self-canonicalize independently (stronger overlap signal)"],
  canonical_situation: "self_canonical_both",
  recommended_action: "DIFFERENTIATE",
  recommendation: "Review whether these pages should target distinct search intents; differentiate titles/H1s and supporting content.",
  related_opportunity_id: "content_cluster:a-b",
};

const NEARDUP_CLUSTER = {
  id: "cluster:old-new", type: "near_duplicate", label: "Near-duplicate content",
  severity: "High", confidence: "high",
  pages: [
    { url: "https://example.com/old-service", title: "Old Service", h1: "Old Service",
      word_count: 400, canonical: null },
    { url: "https://example.com/new-service", title: "New Service", h1: "New Service",
      word_count: 410, canonical: null },
  ],
  evidence: ["Content similarity: 91% (word-trigram overlap)"],
  canonical_situation: "mixed",
  recommended_action: "CONSOLIDATE",
  recommendation: "Consider consolidating these near-duplicate pages if they serve the same purpose, or differentiate them meaningfully if they don't.",
  related_opportunity_id: "content_cluster:old-new",
};

const FREE_CI = {
  available: true, preview: true,
  summary: { pages_analyzed: 42, pages_excluded: 3, duplicate_clusters: 1, overlap_clusters: 0,
            potential_cannibalization_clusters: 1, duplicate_elements: 1, thin_pages: 0 },
  clusters: [CANNIBAL_CLUSTER],
  locked_cluster_count: 1,
  duplicate_elements: [{ type: "duplicate_title", value: "best seo services", urls: ["https://example.com/seo", "https://example.com/seo-2"] }],
  locked_duplicate_element_count: 3,
  thin_pages: [], locked_thin_page_count: 2,
  excluded_pages: [],
};

const PAID_CI = {
  available: true,
  summary: { pages_analyzed: 42, pages_excluded: 3, duplicate_clusters: 1, overlap_clusters: 0,
            potential_cannibalization_clusters: 1, duplicate_elements: 1, thin_pages: 1 },
  clusters: [CANNIBAL_CLUSTER, NEARDUP_CLUSTER],
  duplicate_elements: [{ type: "duplicate_title", value: "best seo services", urls: ["https://example.com/seo", "https://example.com/seo-2"] }],
  thin_pages: [{ url: "https://example.com/thin", word_count: 45, site_median_word_count: 380 }],
  excluded_pages: [{ url: "https://example.com/404page", reason: "insufficient_content_evidence" }],
};

const EMPTY_CI = {
  available: true,
  summary: { pages_analyzed: 5, pages_excluded: 0, duplicate_clusters: 0, overlap_clusters: 0,
            potential_cannibalization_clusters: 0, duplicate_elements: 0, thin_pages: 0 },
  clusters: [], duplicate_elements: [], thin_pages: [], excluded_pages: [],
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

describe("ReportView Content Intelligence section", () => {
  it("free: renders section, summary counters, cannibalization cluster, duplicate elements + locked counts", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: FREE_CI });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Content Intelligence")).toBeTruthy());
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getAllByText("Potential cannibalization").length).toBeGreaterThan(0);
    expect(screen.getByText("https://example.com/seo-services")).toBeTruthy();
    expect(screen.getByText(/Similar titles/)).toBeTruthy();
    expect(screen.getByText(/1 more content cluster/)).toBeTruthy();
    expect(screen.getByText(/duplicated across 2 pages/)).toBeTruthy();
    expect(screen.getByText(/3 more duplicate-element group/)).toBeTruthy();
    expect(screen.getByText(/2 more thin page/)).toBeTruthy();
    // locked near-duplicate cluster never rendered
    expect(screen.queryByText("Near-duplicate content")).toBeNull();
  });

  it("paid: renders near-duplicate cluster + thin pages, evidence toggle expands page details", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: PAID_CI });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Near-duplicate content")).toBeTruthy());
    expect(screen.getByText(/Content similarity: 91%/)).toBeTruthy();
    expect(screen.getAllByText("How to fix").length).toBe(2);
    expect(screen.getByText(/consolidating these near-duplicate/)).toBeTruthy();
    expect(screen.getByText("https://example.com/thin")).toBeTruthy();
    expect(screen.queryByText(/more content cluster/)).toBeNull();

    const toggles = screen.getAllByText(/Show page details/);
    fireEvent.click(toggles[0]);
    expect(screen.getAllByText(/no title|SEO Services|Old Service/).length).toBeGreaterThan(0);
  });

  it("empty state (no clusters/elements/thin pages) renders cleanly without fabricating problems", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: EMPTY_CI });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Content Intelligence")).toBeTruthy());
    // summary counters (always shown) render, but no cluster cards / recommendations exist
    expect(screen.queryByText("Recommended:")).toBeNull();
    expect(screen.queryByText(/Content similarity:/)).toBeNull();
  });

  it("single-page scan: the section renders nothing at all", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT, content_intelligence: { available: false, reason: "multi_page_crawl_required" },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Executive summary")).toBeTruthy());
    expect(screen.queryByText("Content Intelligence")).toBeNull();
  });

  it("incomplete scan: shows an honest 'scan in progress' state, never a fabricated diagnosis", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT, content_intelligence: { available: false, reason: "scan_incomplete" },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Content Intelligence")).toBeTruthy());
    expect(screen.getByText(/insufficient evidence to analyze content overlap/)).toBeTruthy();
  });

  it("never claims confirmed Google cannibalization — strictly 'potential' technical evidence", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: PAID_CI });
    getReportAccess.mockResolvedValue({ unlocked: true });

    const { container } = render(<ReportView scanId="s1" />);
    await waitFor(() => expect(screen.getByText("Near-duplicate content")).toBeTruthy());

    const text = container.textContent.toLowerCase();
    expect(text).not.toMatch(/competing in google/);
    expect(text).not.toMatch(/google penalty/);
    expect(text).not.toMatch(/confirmed cannibalization/);
  });

  it("clusters with multiple URLs stay understandable — every page in the cluster is listed", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: PAID_CI });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);
    await waitFor(() => expect(screen.getByText("https://example.com/seo-services")).toBeTruthy());
    expect(screen.getByText("https://example.com/seo-agency")).toBeTruthy();
    expect(screen.getByText("https://example.com/old-service")).toBeTruthy();
    expect(screen.getByText("https://example.com/new-service")).toBeTruthy();
  });

  it("Phase C: a KEEP_SEPARATE cluster (scanner reviewed it, found no actionable overlap) never becomes a diagnosis card", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, content_intelligence: {
      available: true, thin_pages: [],
      summary: { pages_analyzed: 10, pages_excluded: 0, duplicate_clusters: 0, overlap_clusters: 1,
                potential_cannibalization_clusters: 0, duplicate_elements: 0, thin_pages: 0 },
      clusters: [{
        id: "cluster:x-y", type: "content_overlap", label: "Content overlap", severity: "Medium",
        confidence: "medium", recommended_action: "KEEP_SEPARATE",
        recommendation: "Evidence suggests these pages serve genuinely distinct purposes.",
        evidence: ["Content similarity: 22% (word-trigram overlap)"],
        pages: [{ url: "https://example.com/x" }, { url: "https://example.com/y" }],
      }],
    } });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Content Intelligence")).toBeTruthy());
    // "Content overlap" still legitimately appears once, as the summary counter's own
    // label — but no diagnosis CARD (page URLs, recommendation text) is ever rendered
    // for a KEEP_SEPARATE cluster.
    expect(document.querySelectorAll(".au-rep-card").length).toBe(0);
    expect(screen.queryByText("https://example.com/x")).toBeNull();
    expect(screen.queryByText(/genuinely distinct purposes/)).toBeNull();
  });
});
