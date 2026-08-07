/* Read-only (public share) mode must render the report but expose NO action-capable
   control — no exports, no Share, no upgrade/unlock CTAs — so a public viewer cannot
   trigger any authenticated call. (Absence in the DOM, not merely CSS-hidden.) */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ReportView from "./ReportView.jsx";

const REPORT = {
  domain: "example.com",
  url: "https://example.com/",
  scanned_at: "2026-01-01T00:00:00Z",
  scorecard: {
    overall_score: 62, grade: "C", status: "ok",
    category_scores: [], strengths: [], weaknesses: [], quick_wins: [], top_priorities: [],
    issue_counts: { Critical: 1, High: 0, Medium: 2, Low: 0 }, summary: "Test summary.",
  },
  recommendations: [{
    id: "schema", issue_title: "Add structured data", category: "AI Extractability",
    severity: "High", priority: "Critical", priority_score: 3, score: 15,
    difficulty: "Easy", estimated_fix_time: "30m", description: "No JSON-LD found.",
    business_impact: "x", ai_visibility_impact: "y", fix_template: {},
  }],
  recommendation_count: 1,
  ai: null,
};

describe("ReportView readOnly", () => {
  it("renders the report but no action controls", () => {
    render(<ReportView readOnly report={REPORT} />);

    // Content is present…
    expect(screen.getByText("example.com")).toBeTruthy();
    expect(screen.getByText(/Add structured data/)).toBeTruthy();

    // …but every action-capable control is absent from the DOM.
    expect(screen.queryByText("PDF")).toBeNull();
    expect(screen.queryByText("JSON")).toBeNull();
    expect(screen.queryByText("CSV")).toBeNull();
    expect(screen.queryByText("Share")).toBeNull();
    expect(screen.queryByText(/Unlock/i)).toBeNull();
  });
});
