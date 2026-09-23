/* Technical SEO & Indexability section in ReportView. Server-side gated
   (report.technical_seo, see backend `gate_technical_seo`) — free/paid is asserted by
   what the mocked API RETURNS, matching the pattern in ReportView.phase4.test.jsx: the
   component renders whatever it's given, it never slices/blurs real content
   client-side. Terminology is asserted to stay strictly technical — no claim that
   Google has actually indexed anything. */
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
  phase4: { available: false, reason: "scan_incomplete" },
};

const FREE_TSEO = {
  available: true, preview: true,
  summary: { total_urls: 6, indexable: 3, not_indexable: 1, blocked: 0, redirected: 1, errors: 1, unknown: 0 },
  issues: [
    { code: "ERROR_4XX", severity: "High", count: 1, label: "1 URL returns 4xx",
      affected_urls: ["https://example.com/broken"] },
    { code: "NOINDEX_META", severity: "High", count: 1, label: "1 URL uses meta robots noindex",
      affected_urls: ["https://example.com/hidden"] },
  ],
  locked_issue_count: 3,
  urls: [
    { url: "https://example.com/broken", status_code: 404, crawlable: true, indexable: false,
      meta_robots: [], x_robots_tag: [], canonical: null, canonical_type: "missing",
      canonical_target_status: null, redirect: false, redirect_chain: [],
      final_url: "https://example.com/broken", sitemap: true,
      indexability_status: "error", issues: ["ERROR_4XX"],
      evidence: ["HTTP status: 404", "canonical: missing", "sitemap: present"] },
  ],
  locked_url_count: 5,
  sitemap_urls_not_crawled: [], locked_sitemap_gap_count: 2,
};

const PAID_TSEO = {
  available: true,
  summary: FREE_TSEO.summary,
  issues: [
    ...FREE_TSEO.issues,
    { code: "REDIRECT_CHAIN", severity: "Medium", count: 1, label: "1 URL redirects through multiple hops",
      affected_urls: ["https://example.com/old"] },
  ],
  urls: [
    ...FREE_TSEO.urls,
    { url: "https://example.com/hidden", status_code: 200, crawlable: true, indexable: false,
      meta_robots: ["noindex"], x_robots_tag: [], canonical: "https://example.com/",
      canonical_type: "other", canonical_target_status: 200, redirect: false, redirect_chain: [],
      final_url: "https://example.com/hidden", sitemap: false,
      indexability_status: "not_indexable", issues: ["NOINDEX_META"],
      evidence: ["HTTP status: 200", "meta robots: noindex", "canonical: points to https://example.com/",
                "sitemap: not present"] },
    { url: "https://example.com/", status_code: 200, crawlable: true, indexable: true,
      meta_robots: [], x_robots_tag: [], canonical: "https://example.com/", canonical_type: "self",
      canonical_target_status: null, redirect: false, redirect_chain: [],
      final_url: "https://example.com/", sitemap: true,
      indexability_status: "indexable", issues: [],
      evidence: ["HTTP status: 200", "canonical: self", "sitemap: present"] },
  ],
  sitemap_urls_not_crawled: [],
};

const EMPTY_TSEO = {
  available: true,
  summary: { total_urls: 1, indexable: 1, not_indexable: 0, blocked: 0, redirected: 0, errors: 0, unknown: 0 },
  issues: [],
  urls: [
    { url: "https://example.com/", status_code: 200, crawlable: true, indexable: true,
      meta_robots: [], x_robots_tag: [], canonical: "https://example.com/", canonical_type: "self",
      canonical_target_status: null, redirect: false, redirect_chain: [],
      final_url: "https://example.com/", sitemap: true,
      indexability_status: "indexable", issues: [],
      evidence: ["HTTP status: 200", "canonical: self", "sitemap: present"] },
  ],
  sitemap_urls_not_crawled: [],
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

describe("ReportView Technical SEO & Indexability section", () => {
  it("free: renders the section, summary counters, issues, and a real URL preview + locked counts", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: FREE_TSEO });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Technical SEO & Indexability")).toBeTruthy());
    // summary counters
    expect(screen.getByText("6")).toBeTruthy();          // total URLs
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    // issues
    expect(screen.getByText("1 URL returns 4xx")).toBeTruthy();
    expect(screen.getByText("1 URL uses meta robots noindex")).toBeTruthy();
    expect(screen.getByText(/3 more technical issue/)).toBeTruthy();
    // affected URL preview (the issue's own Evidence step cross-references the real
    // per-URL HTTP status from data.urls — never a bare, context-free URL)
    expect(screen.getByText("https://example.com/broken — HTTP 404")).toBeTruthy();
    expect(screen.getAllByText(/HTTP 404/).length).toBeGreaterThan(0);
    expect(screen.getByText(/5 more crawled URL/)).toBeTruthy();
    // the URL-by-URL table's locked rows never leak: "hidden" is real free-tier data
    // on the NOINDEX_META issue's own affected_urls (legitimately shown as Evidence
    // for that issue — the server already sent it as part of the free preview), but
    // it must have no corresponding URL-TABLE row (that table only has "broken" in
    // the free tier) — i.e. it's evidence for an issue, never full per-URL detail.
    const tableUrls = Array.from(document.querySelectorAll(".au-tseo-url")).map((n) => n.textContent);
    expect(tableUrls).toEqual(["https://example.com/broken"]);
  });

  it("paid: renders full issue list and full URL table, no lock affordances", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: PAID_TSEO });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("https://example.com/hidden")).toBeTruthy());
    expect(screen.getByText("1 URL redirects through multiple hops")).toBeTruthy();
    expect(screen.getByText("https://example.com/")).toBeTruthy();
    expect(screen.queryByText(/more technical issue/)).toBeNull();
    expect(screen.queryByText(/more crawled URL/)).toBeNull();
    // evidence-grounded canonical relationship display
    expect(screen.getByText(/Canonical → https:\/\/example\.com\//)).toBeTruthy();
  });

  it("empty/no-issues state renders cleanly without fabricating problems", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: EMPTY_TSEO });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Technical SEO & Indexability")).toBeTruthy());
    expect(screen.getByText("https://example.com/")).toBeTruthy();
    expect(screen.queryByText(/URL returns/)).toBeNull();
  });

  it("incomplete scan: shows an honest 'scan in progress' state, never a fabricated diagnosis", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: { available: false, reason: "scan_incomplete" } });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Technical SEO & Indexability")).toBeTruthy());
    expect(screen.getByText(/insufficient evidence to generate indexability intelligence/)).toBeTruthy();
    expect(screen.queryByText("https://example.com/")).toBeNull();
  });

  it("never claims actual Google indexing — strictly technical terminology only", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: PAID_TSEO });
    getReportAccess.mockResolvedValue({ unlocked: true });

    const { container } = render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("https://example.com/hidden")).toBeTruthy());

    // Scoped to the Technical SEO section itself (#rep-technical-seo): it must never
    // claim actual Google indexing/ranking from its own (crawl-only) evidence.
    const text = container.querySelector("#rep-technical-seo").textContent.toLowerCase();
    expect(text).not.toMatch(/google indexed/);
    expect(text).not.toMatch(/indexed by google/);
    expect(text).not.toMatch(/search console/);
    expect(text).not.toMatch(/google ranking/);
  });

  it("Phase C: 'sitemap URLs not crawled' renders as scan-coverage information, never as an indexability error", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: {
      ...EMPTY_TSEO, sitemap_urls_not_crawled: ["https://example.com/deep-page"], locked_sitemap_gap_count: 0,
    } });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("Scan coverage — not an indexability error")).toBeTruthy());
    expect(screen.getByText(/not reached during this scan's crawl/)).toBeTruthy();
    expect(screen.getByText("https://example.com/deep-page")).toBeTruthy();
    // it must never be classified as one of the "issues" (severity-badged problems)
    const coverage = screen.getByText("Scan coverage — not an indexability error").closest(".au-tseo-coverage");
    expect(coverage.querySelector(".au-tseo-badge")).toBeNull();
  });

  it("Phase C: a technical issue already covered by an existing recommendation shows as supporting evidence, with a jump-link, never a duplicate action", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [{
        id: "robots", issue_title: "Fix robots.txt blocking", category: "Crawlability",
        severity: "High", priority: "High", priority_score: 5, score: 30, weight: 10, status: "fail",
        description: "robots.txt blocks a key page.", business_impact: "x", ai_visibility_impact: "y",
        estimated_fix_time: "15m", difficulty: "Easy", fix_template: { recommended_fix: ["Edit robots.txt"] },
      }],
      technical_seo: {
        ...FREE_TSEO, preview: undefined, locked_issue_count: undefined, locked_url_count: undefined,
        issues: [{ code: "ROBOTS_BLOCKED", severity: "High", count: 1, label: "1 URL blocked by robots.txt",
                  affected_urls: ["https://example.com/blocked"] }],
      },
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/Already covered by/)).toBeTruthy());
    expect(screen.getByText(/"Fix robots.txt blocking" recommendation/)).toBeTruthy();
    expect(screen.getByText("1 URL blocked by robots.txt")).toBeTruthy();
    // never a second, duplicate "How to fix" diagnosis card for the same root cause
    expect(screen.queryByText("How to fix")).toBeNull();
  });

  it("Phase C: Verify after re-scan is never offered for a technical issue with no covering recommendation", async () => {
    getReport.mockResolvedValue({ ...BASE_REPORT, technical_seo: FREE_TSEO });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("1 URL returns 4xx")).toBeTruthy());
    expect(screen.queryByText(/Verify after re-scan/)).toBeNull();
  });
});
