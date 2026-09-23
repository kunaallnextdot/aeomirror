/* Question Bank — pure rendering tests. Server-side gated (see backend
   gate_question_bank): the component renders exactly what it's given, it never
   slices/blurs real content client-side. No network — the pure component under test
   here is ReportQuestionBank, mocked at the api.js boundary (same pattern as
   ReportView.recommendations.test.jsx / ReportView.phase4.test.jsx). */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  getQuestionBank: vi.fn(),
  ScanError: class ScanError extends Error {},
}));

import { getQuestionBank } from "../api.js";
import { ReportQuestionBank } from "./QuestionBank.jsx";

const TRACKED_MENTIONED = {
  question: "What is the best CRM?", sources: ["scan_faq", "answer_tracking"],
  source_urls: ["https://acme.example/faq"], category: "informational",
  answer_tracking: { tracked: true, mention_rate: 60.0, is_gap: false, average_position: null },
  evidence_count: 2, related_opportunity_ids: [],
};
const TRACKED_GAP = {
  question: "How long does treatment take?", sources: ["answer_tracking"],
  source_urls: [], category: "informational",
  answer_tracking: { tracked: true, mention_rate: 0.0, is_gap: true, average_position: null },
  evidence_count: 1, related_opportunity_ids: ["answer_gap:p2"],
};
const UNTRACKED = {
  question: "How much does treatment cost?", sources: ["scan_heading"],
  source_urls: ["https://acme.example/pricing"], category: "pricing",
  answer_tracking: { tracked: false, mention_rate: null, is_gap: null, average_position: null },
  evidence_count: 1, related_opportunity_ids: ["question_opportunity:how much does treatment cost"],
};

describe("ReportQuestionBank", () => {
  it("renders questions with source, tracked-mentioned, and related-opportunity info", async () => {
    getQuestionBank.mockResolvedValue({
      available: true, monitor_id: "m1",
      questions: [TRACKED_MENTIONED, UNTRACKED], total_count: 2,
    });
    render(<ReportQuestionBank scanId="s1" />);

    await waitFor(() => expect(screen.getByText("Question Bank")).toBeTruthy());
    // Phase I: the subtitle states the "why both sections exist" mental model concisely
    expect(screen.getByText(/your complete question set — every opportunity above/)).toBeTruthy();
    expect(screen.getByText("What is the best CRM?")).toBeTruthy();
    expect(screen.getByText("How much does treatment cost?")).toBeTruthy();
    expect(screen.getByText(/Mentioned in 60/)).toBeTruthy();
    expect(screen.getByText(/Not yet tracked in Answer Tracking/)).toBeTruthy();
    expect(screen.getByText(/1 related opportunity/)).toBeTruthy();
    expect(screen.getByText("FAQ schema")).toBeTruthy();
    expect(screen.getByText("Page heading")).toBeTruthy();
  });

  it("shows gap/weak-visibility state distinctly from mentioned state", async () => {
    getQuestionBank.mockResolvedValue({
      available: true, monitor_id: "m1", questions: [TRACKED_GAP], total_count: 1,
    });
    render(<ReportQuestionBank scanId="s1" />);

    await waitFor(() => expect(screen.getByText(/Not mentioned in tracked AI answers/)).toBeTruthy());
    expect(screen.queryByText(/Mentioned in/)).toBeNull();
  });

  it("empty state: no questions found renders nothing (no fabricated content)", async () => {
    getQuestionBank.mockResolvedValue({ available: true, monitor_id: null, questions: [], total_count: 0 });
    const { container } = render(<ReportQuestionBank scanId="s1" />);
    await waitFor(() => expect(getQuestionBank).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("incomplete scan shows an honest 'scan in progress' state", async () => {
    getQuestionBank.mockResolvedValue({ available: false, reason: "scan_incomplete" });
    render(<ReportQuestionBank scanId="s1" />);
    await waitFor(() => expect(screen.getByText(/Scan still in progress/)).toBeTruthy());
  });

  it("free/locked state: shows the real preview + a locked count, not the full dataset", async () => {
    getQuestionBank.mockResolvedValue({
      available: true, monitor_id: null, preview: true, locked_question_count: 4,
      questions: [TRACKED_MENTIONED], total_count: 5,
    });
    render(<ReportQuestionBank scanId="s1" />);

    await waitFor(() => expect(screen.getByText("What is the best CRM?")).toBeTruthy());
    expect(screen.getByText(/4 more question/)).toBeTruthy();
    expect(screen.getByText("Unlock")).toBeTruthy();
    // only the ONE real preview question is in the DOM — nothing hidden/blurred
    expect(screen.queryAllByText(/\?$/).length).toBe(1);
  });

  it("paid/unlocked state: shows the complete dataset with no lock affordance", async () => {
    getQuestionBank.mockResolvedValue({
      available: true, monitor_id: "m1",
      questions: [TRACKED_MENTIONED, TRACKED_GAP, UNTRACKED], total_count: 3,
    });
    render(<ReportQuestionBank scanId="s1" />);

    await waitFor(() => expect(screen.getByText("What is the best CRM?")).toBeTruthy());
    expect(screen.getByText("How long does treatment take?")).toBeTruthy();
    expect(screen.getByText("How much does treatment cost?")).toBeTruthy();
    expect(screen.queryByText(/more question/)).toBeNull();
    expect(screen.queryByText("Unlock")).toBeNull();
  });

  it("does not duplicate Answer Tracking's per-provider/per-sample dashboard", async () => {
    getQuestionBank.mockResolvedValue({
      available: true, monitor_id: "m1", questions: [TRACKED_MENTIONED], total_count: 1,
    });
    render(<ReportQuestionBank scanId="s1" />);
    await waitFor(() => expect(screen.getByText("What is the best CRM?")).toBeTruthy());
    // No per-sample/per-provider verdict UI (that's AnswerTracking.jsx's job, not this).
    expect(screen.queryByText(/MENTIONED|NOT MENTIONED/)).toBeNull();
    expect(screen.queryByText(/View full response/)).toBeNull();
  });
});
