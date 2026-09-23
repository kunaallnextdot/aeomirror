/* Real Crawl Graph & Internal Linking section in ReportView. Server-side gated
   (report.crawl_graph, see backend `gate_crawl_graph`) — free/paid is asserted by
   what the mocked API RETURNS, matching the pattern in
   ReportView.technical_seo.test.jsx: the component renders whatever it's given, it
   never slices/blurs real content client-side. Terminology is asserted to stay
   strictly about the SITE'S OWN internal-link graph — no claim that Google crawled or
   indexed anything. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
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
};

const FREE_CG = {
  available: true, preview: true,
  seed_url: "https://example.com/",
  summary: { pages: 12, internal_edges: 18, reachable_pages: 9, disconnected_pages: 1,
            orphan_pages: 2, max_depth: 3, unknown_reachability_pages: 0 },
  issues: [
    { code: "ORPHAN_PAGE", severity: "High", count: 2, label: "2 crawled pages have zero inbound internal links",
      affected_urls: ["https://example.com/legacy-service", "https://example.com/old-article"] },
  ],
  locked_issue_count: 2,
  orphans: [
    { url: "https://example.com/legacy-service", is_seed: false, inbound_pages: 0, outbound_pages: 2,
      inbound_links: 0, outbound_links: 2, reachable: true, depth: 2, orphan: true, referring_anchors: [] },
  ],
  locked_orphan_count: 1,
  pages: [
    { url: "https://example.com/legacy-service", is_seed: false, inbound_pages: 0, outbound_pages: 2,
      inbound_links: 0, outbound_links: 2, reachable: true, depth: 2, orphan: true, referring_anchors: [] },
  ],
  locked_page_count: 11,
  edges: [], locked_edge_count: 18,
  depth_histogram: [{ label: "0", count: 1 }, { label: "1", count: 6 }, { label: "2", count: 4 }, { label: "3", count: 1 }],
  top_referenced: [{ url: "https://example.com/services", inbound_pages: 5 }],
  locked_top_referenced_count: 2,
  unresolved_internal_targets: [], locked_unresolved_target_count: 0,
};

const PAID_CG = {
  available: true,
  seed_url: "https://example.com/",
  summary: FREE_CG.summary,
  issues: [
    ...FREE_CG.issues,
    { code: "DISCONNECTED_PAGE", severity: "Medium", count: 1,
      label: "1 crawled page is not reachable from the crawl seed",
      affected_urls: ["https://example.com/island"] },
  ],
  orphans: [
    ...FREE_CG.orphans,
    { url: "https://example.com/old-article", is_seed: false, inbound_pages: 0, outbound_pages: 0,
      inbound_links: 0, outbound_links: 0, reachable: true, depth: 1, orphan: true, referring_anchors: [] },
  ],
  pages: [
    ...FREE_CG.pages,
    { url: "https://example.com/services", is_seed: false, inbound_pages: 5, outbound_pages: 3,
      inbound_links: 6, outbound_links: 3, reachable: true, depth: 1, orphan: false,
      referring_anchors: [{ text: "our services", count: 4 }] },
  ],
  edges: [{ source: "https://example.com/", target: "https://example.com/services", anchor_text: "services" }],
  depth_histogram: FREE_CG.depth_histogram,
  top_referenced: [{ url: "https://example.com/services", inbound_pages: 5 }, { url: "https://example.com/about", inbound_pages: 3 }],
  unresolved_internal_targets: [],
};

const EMPTY_CG = {
  available: true,
  seed_url: "https://example.com/",
  summary: { pages: 1, internal_edges: 0, reachable_pages: 1, disconnected_pages: 0,
            orphan_pages: 0, max_depth: 0, unknown_reachability_pages: 0 },
  issues: [],
  orphans: [],
  pages: [{ url: "https://example.com/", is_seed: true, inbound_pages: 0, outbound_pages: 0,
           inbound_links: 0, outbound_links: 0, reachable: true, depth: 0, orphan: null, referring_anchors: [] }],
  edges: [],
  depth_histogram: [{ label: "0", count: 1 }],
  top_referenced: [],
  unresolved_internal_targets: [],
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

describe("ReportView Real Crawl Graph & Internal Linking section", () => {
  it("free: renders section, summary counters, issues, orphan preview, depth summary + locked counts", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: FREE_CG });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Crawl Graph & Internal Linking")).toBeTruthy());
    expect(screen.getByText("12")).toBeTruthy();             // pages crawled
    expect(screen.getByText("2 crawled pages have zero inbound internal links")).toBeTruthy();
    expect(screen.getByText(/2 more graph issue/)).toBeTruthy();
    expect(screen.getByText("https://example.com/legacy-service")).toBeTruthy();
    expect(screen.getByText(/0 referring pages/)).toBeTruthy();
    expect(screen.getByText(/1 more orphan page/)).toBeTruthy();
    expect(screen.getByText("Depth 0")).toBeTruthy();
    expect(screen.getByText("Depth 3")).toBeTruthy();
    // the locked orphan gets no table row of its own (it's real free-tier data on the
    // ORPHAN_PAGE issue's own affected_urls — legitimately shown there as Evidence —
    // but never the full per-page orphan-table detail beyond the free preview)
    const tableUrls = Array.from(document.querySelectorAll(".au-tseo-url")).map((n) => n.textContent);
    expect(tableUrls).not.toContain("https://example.com/old-article");
  });

  it("paid: renders full issue list, full orphan list, and edges are available (no lock affordances)", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: PAID_CG });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("https://example.com/old-article")).toBeTruthy());
    expect(screen.getByText("1 crawled page is not reachable from the crawl seed")).toBeTruthy();
    expect(screen.getByText("https://example.com/about")).toBeTruthy();     // 2nd top-referenced page
    expect(screen.queryByText(/more graph issue/)).toBeNull();
    expect(screen.queryByText(/more orphan page/)).toBeNull();
  });

  it("empty graph state (no issues, no orphans) renders cleanly without fabricating problems", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: EMPTY_CG });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Crawl Graph & Internal Linking")).toBeTruthy());
    expect(screen.getByText("Depth 0")).toBeTruthy();
    expect(screen.queryByText(/zero inbound/)).toBeNull();
  });

  it("single-page scan: the section renders nothing at all (never a fabricated 'this page is an orphan' claim)", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT, crawl_graph: { available: false, reason: "multi_page_crawl_required" },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Executive summary")).toBeTruthy());
    expect(screen.queryByText("Crawl Graph & Internal Linking")).toBeNull();
    expect(screen.queryByText(/orphan/i)).toBeNull();
  });

  it("incomplete scan: shows an honest 'scan in progress' state, never a fabricated diagnosis", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT, crawl_graph: { available: false, reason: "scan_incomplete" },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Crawl Graph & Internal Linking")).toBeTruthy());
    expect(screen.getByText(/insufficient evidence to build the internal-link graph/)).toBeTruthy();
  });

  it("never claims Google crawled/indexed anything — strictly the site's own graph", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: PAID_CG });
    getReportAccess.mockResolvedValue({ unlocked: true });

    const { container } = render(<ReportView scanId="s1" />);
    await waitFor(() => expect(screen.getByText("https://example.com/old-article")).toBeTruthy());

    const text = container.textContent.toLowerCase();
    expect(text).not.toMatch(/google crawled/);
    expect(text).not.toMatch(/google indexed/);
    expect(text).not.toMatch(/pagerank/);
  });

  it("UI remains usable without a visual graph — tables/lists carry the full diagnostic content", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: PAID_CG });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);
    await waitFor(() => expect(screen.getByText("Top referenced pages")).toBeTruthy());
    // no canvas/svg graph-visualization element required to read the evidence
    expect(document.querySelector("canvas")).toBeNull();
    expect(screen.getByText("https://example.com/services")).toBeTruthy();
    expect(screen.getByText(/5 referring pages/)).toBeTruthy();
  });

  it("Phase C: ORPHAN_PAGE and DISCONNECTED_PAGE get distinct, specific Why-it-matters text — never conflated", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: {
      ...PAID_CG,
      issues: [
        { code: "ORPHAN_PAGE", severity: "High", count: 1, label: "1 crawled page has zero inbound internal links",
          affected_urls: ["https://example.com/legacy-service"] },
        { code: "DISCONNECTED_PAGE", severity: "Medium", count: 1, label: "1 crawled page is not reachable from the crawl seed",
          affected_urls: ["https://example.com/island"] },
      ],
    } });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("1 crawled page has zero inbound internal links")).toBeTruthy());
    // orphan: zero inbound links at all
    expect(screen.getByText(/No other crawled page links to these URLs/)).toBeTruthy();
    // disconnected: may still have inbound links, just not reachable from the seed
    expect(screen.getByText(/aren't reachable by following links from the crawl seed/)).toBeTruthy();
  });

  it("Phase C: Verify after re-scan is never offered for a crawl-graph issue (no related_recommendation_id exists on the backend)", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, crawl_graph: FREE_CG });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<ReportView scanId="s1" />);

    await waitFor(() => expect(screen.getByText("2 crawled pages have zero inbound internal links")).toBeTruthy());
    expect(screen.queryByText(/Verify after re-scan/)).toBeNull();
  });
});
