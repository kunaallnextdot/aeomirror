/* Decision 3 regression: ReportView's Recommendations section now renders exactly what
   the server sends (server-side `gate_recommendations` trimming, not client-side blur).
   A locked/free response already contains ONLY the free preview + a locked count — there
   is no hidden real content in the DOM to find, unlike the old blurred-real-content
   pattern this replaces. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

const REC = (id, title, note, overrides = {}) => ({
  id, issue_title: title, category: "AI Extractability", severity: "High",
  priority: "Critical", priority_score: 3, score: 15, difficulty: "Easy",
  estimated_fix_time: "30m", description: `Diagnosis for ${note}.`,
  business_impact: "x", ai_visibility_impact: "y",
  fix_template: { recommended_fix: [`Locked implementation step ${note}`] },
  ...overrides,
});

const BASE_REPORT = {
  domain: "example.com", url: "https://example.com/", scanned_at: "2026-01-01T00:00:00Z",
  scorecard: {
    overall_score: 62, grade: "C", status: "ok",
    category_scores: [], strengths: [], weaknesses: [], quick_wins: [], top_priorities: [],
    issue_counts: { Critical: 1, High: 0, Medium: 2, Low: 0 }, summary: "Test summary.",
  },
  insights: null,
  ai: null,
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

describe("ReportView recommendations — server-side gating", () => {
  it("free/locked: shows only the free preview + a locked count, no hidden locked content", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [REC("schema", "Add structured data", "schema"), REC("robots", "Fix robots.txt", "robots")],
      recommendation_count: 5, locked_recommendation_count: 3, recommendations_preview: true,
    });
    getReportAccess.mockResolvedValue({ unlocked: false });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/1\. Add structured data/)).toBeTruthy());
    expect(screen.getByText(/2\. Fix robots\.txt/)).toBeTruthy();
    // the locked count is communicated…
    expect(screen.getByText(/3 more fixes found/)).toBeTruthy();
    expect(screen.getByText(/Unlock complete diagnosis/i)).toBeTruthy();
    // …and exactly 2 recommendation cards render — the locked 3 were never part of the
    // API response at all (this is what the backend test in test_gating_cleanup.py
    // verifies never leaves the server; here we only confirm the UI renders no more
    // than what it was given).
    expect(document.querySelectorAll(".au-rep-card").length).toBe(2);
  });

  it("paid/unlocked: shows every recommendation with no lock affordance", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [REC("schema", "Add structured data", "schema"), REC("robots", "Fix robots.txt", "robots"),
                        REC("links", "Improve internal linking", "links")],
      recommendation_count: 3,
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/1\. Add structured data/)).toBeTruthy());
    expect(screen.getByText(/2\. Fix robots\.txt/)).toBeTruthy();
    expect(screen.getByText(/3\. Improve internal linking/)).toBeTruthy();
    expect(screen.queryByText(/more fixes found/)).toBeNull();
    expect(screen.queryByText(/Unlock complete diagnosis/i)).toBeNull();
  });

  it("shows a real Evidence step (the signal's own issue strings + evidence dict) on each expanded recommendation", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [REC("schema", "Add structured data", "schema", {
        evidence: { issues: ["Organization schema missing"], findings: { organization_entity: "not_detected" } },
      })],
      recommendation_count: 1,
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/1\. Add structured data/)).toBeTruthy());
    expect(screen.getByText("Evidence")).toBeTruthy();
    expect(screen.getByText("Organization schema missing")).toBeTruthy();
    expect(screen.getByText(/organization_entity: not_detected/)).toBeTruthy();
  });

  it("no Evidence heading is fabricated when a recommendation has no real evidence content", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [REC("schema", "Add structured data", "schema")],   // no `evidence` field
      recommendation_count: 1,
    });
    getReportAccess.mockResolvedValue({ unlocked: true });

    render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/1\. Add structured data/)).toBeTruthy());
    expect(screen.queryByText("Evidence")).toBeNull();
  });

  it("a section anchor already in the URL (e.g. a deep link from Action Center) scrolls that real section into view", async () => {
    getReport.mockResolvedValue({
      ...BASE_REPORT,
      recommendations: [REC("schema", "Add structured data", "schema")],
      recommendation_count: 1, locked_recommendation_count: 0, recommendations_preview: false,
    });
    getReportAccess.mockResolvedValue({ unlocked: true });
    const scrollSpy = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy;
    window.location.hash = "#rep-recommendations";

    try {
      render(<MemoryRouter><ReportView scanId="s1" /></MemoryRouter>);
      await waitFor(() => expect(screen.getByText(/1\. Add structured data/)).toBeTruthy());
      await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
      expect(scrollSpy.mock.instances[0].id).toBe("rep-recommendations");
    } finally {
      Element.prototype.scrollIntoView = original;
      window.location.hash = "";
    }
  });
});
