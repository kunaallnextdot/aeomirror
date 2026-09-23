/* AEO Answer Simulator — pure rendering + interaction tests, api.js mocked at the
   boundary (same convention as QuestionBank.test.jsx). No network. */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  getSimulatorQuestions: vi.fn(),
  runAnswerSimulation: vi.fn(),
  explainSimulatorQuestion: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("./UpgradeModal.jsx", () => ({
  useUpgrade: () => ({ handleGated: () => false }),
}));

import {
  getSimulatorQuestions, runAnswerSimulation, explainSimulatorQuestion, ScanError,
} from "../api.js";
import AnswerSimulator from "./AnswerSimulator.jsx";

const QUESTIONS = {
  monitor_id: "m1",
  bank_questions: [
    { question: "What pricing plans are available?", already_tracked: false, category: "commercial" },
    { question: "What is AI attendance?", already_tracked: false, category: "informational" },
  ],
  tracked_prompts: [],
};

const RESULT_HIGH = {
  prompt_id: "p1", question: "What pricing plans are available?",
  answerability: "HIGH", evidence_coverage_pct: 87.5, evidence_relevance_pct: 91.2,
  topic_alignment_score: 82.4, supported_url_count: 2,
  answer_text: "Based on the website's currently scanned content:\n- We offer Basic and Pro plans. (source: https://acme.example/pricing)",
  brand_mentioned: true, mention_context: null,
  evidence: [
    { url: "https://acme.example/pricing", field: "faq_answer", snippet: "We offer Basic and Pro plans.", evidence_relevance_score: 9.1 },
    { url: "https://acme.example/faq", field: "body_chunk", snippet: "Pricing starts at $10/month.", evidence_relevance_score: 5.2 },
  ],
  missing_information: [], confidence: "high", llm_step_used: false, model: "deterministic",
};

const RESULT_INSUFFICIENT = {
  prompt_id: "p2", question: "spacecraft propulsion question",
  answerability: "INSUFFICIENT_EVIDENCE", evidence_coverage_pct: 0, evidence_relevance_pct: 0,
  topic_alignment_score: 0, supported_url_count: 0,
  answer_text: "We couldn't find meaningful evidence on the website for this question.",
  brand_mentioned: false, mention_context: null, evidence: [],
  missing_information: ["This topic is not currently supported by the website's content."],
  confidence: "low", llm_step_used: false, model: "deterministic",
};

const RESULT_OFF_TOPIC = {
  prompt_id: "p4", question: "What is the best chocolate lava cake recipe?",
  answerability: "INSUFFICIENT_EVIDENCE", evidence_coverage_pct: 0, evidence_relevance_pct: 0,
  topic_alignment_score: 4.2, supported_url_count: 0,
  answer_text: "We couldn't find meaningful evidence on the website for this question.",
  brand_mentioned: false, mention_context: null, evidence: [],
  missing_information: ["This topic is not currently supported by the website's content."],
  confidence: "low", llm_step_used: false, model: "deterministic",
};

const RESULT_LOW = {
  prompt_id: "p5", question: "How much does attendance software cost per month?",
  answerability: "LOW", evidence_coverage_pct: 22, evidence_relevance_pct: 18,
  topic_alignment_score: 48, supported_url_count: 1,
  answer_text: "We found some relevant information, but the website does not provide "
    + "enough evidence for a strong answer.\n- We offer attendance software for colleges. (source: https://acme.example/)",
  brand_mentioned: null, mention_context: null,
  evidence: [{ url: "https://acme.example/", field: "title", snippet: "Attendance software", evidence_relevance_score: 2.1 }],
  missing_information: [], confidence: "low", llm_step_used: false, model: "deterministic",
};

const RESULT_LOCKED_PREVIEW = {
  prompt_id: "p3", question: "What is AI attendance?",
  answerability: "MEDIUM", evidence_coverage_pct: 60, evidence_relevance_pct: 55,
  supported_url_count: 1,
  answer_text: "Based on the website's currently scanned content:\n- AI attendance tracks presence automatically.",
  brand_mentioned: null, mention_context: null,
  evidence: [
    { url: "https://acme.example/a", field: "body_chunk", snippet: "AI attendance tracks presence.", evidence_relevance_score: 4.0 },
    { url: "https://acme.example/b", field: "h1", snippet: "AI Attendance", evidence_relevance_score: 3.0 },
  ],
  locked_evidence_count: 3,
  missing_information: [], confidence: "medium", llm_step_used: false, model: "deterministic",
};

describe("AnswerSimulator", () => {
  it("renders the scan-derived question list", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    expect(screen.getByText("What is AI attendance?")).toBeTruthy();
  });

  it("shows the required non-live-measurement disclaimer copy", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText(/estimate how well an AI answer can be supported/)).toBeTruthy());
    expect(screen.getByText(/not a live measurement of ChatGPT, Claude, Gemini, or Perplexity/)).toBeTruthy();
  });

  it("Phase H: every result card carries a visible 'Simulated' tag, always on, not just on hover/expand", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    // visible before expanding the card — not gated behind the expand/collapse toggle
    expect(screen.getByText("Simulated")).toBeTruthy();
  });

  it("adds a custom question and selects it", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"), { target: { value: "custom q?" } });
    fireEvent.click(screen.getByText("Add"));
    expect(screen.getByText("custom q?")).toBeTruthy();
    expect(screen.getByText("1 question selected")).toBeTruthy();
  });

  it("runs a simulation and renders answerability/coverage/answer/brand-mention/evidence", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_HIGH], batch_limit: null, locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));

    await waitFor(() => expect(runAnswerSimulation).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());

    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));
    fireEvent.click(screen.getByText(/View evidence/));
    expect(screen.getByText(/87.5%/)).toBeTruthy();
    expect(screen.getAllByText(/We offer Basic and Pro plans/).length).toBeGreaterThan(0);
    expect(screen.getByText("YES")).toBeTruthy();
    expect(screen.getAllByText(/https:\/\/acme.example\/pricing/).length).toBeGreaterThan(0);
  });

  it("sends question_bank_keys for a selected, untracked bank question (not custom_questions)", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(runAnswerSimulation).toHaveBeenCalled());
    const [, payload] = runAnswerSimulation.mock.calls[0];
    expect(payload.questionBankKeys).toContain("What pricing plans are available?");
    expect(payload.customQuestions).toEqual([]);
  });

  it("shows an insufficient-evidence result with an Explain why button, and forces the LLM step on click", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_INSUFFICIENT], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"), { target: { value: "spacecraft propulsion question" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());

    fireEvent.click(screen.getByText("Not enough evidence").closest(".au-as-card-head"));
    expect(screen.getByText(/This topic is not currently supported/)).toBeTruthy();
    const explainBtn = screen.getByText("Explain why");
    expect(explainBtn).toBeTruthy();

    explainSimulatorQuestion.mockResolvedValue({ ...RESULT_INSUFFICIENT, llm_step_used: true, answer_text: "AI explanation text." });
    fireEvent.click(explainBtn);
    await waitFor(() => expect(explainSimulatorQuestion).toHaveBeenCalledWith("m1", "r1", "p2"));
  });

  it("no-LLM-configured results never show a fake AI-explained badge", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));
    expect(screen.queryByText("AI-explained")).toBeNull();
  });

  it("Free-plan batch truncation shows the locked-count notice", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_HIGH], batch_limit: 5, locked_count: 3,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText(/Free plan batch limit is 5/)).toBeTruthy());
  });

  it("empty knowledge index (no scans yet) shows a clean state, not a fabricated question list", async () => {
    getSimulatorQuestions.mockResolvedValue({ monitor_id: "m1", bank_questions: [], tracked_prompts: [] });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText(/No scan-derived questions yet/)).toBeTruthy());
  });

  it("read-only viewers cannot add custom questions or run a simulation", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    render(<AnswerSimulator monitorId="m1" canRun={false} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    expect(screen.queryByPlaceholderText("Add a custom question…")).toBeNull();
    expect(screen.queryByText("Run Simulation")).toBeNull();
  });

  it("renders evidence relevance %, evidence coverage %, and Supported-by-N-pages", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));

    expect(screen.getByText("Evidence relevance")).toBeTruthy();
    expect(screen.getByText("91.2%")).toBeTruthy();
    expect(screen.getByText("Evidence coverage")).toBeTruthy();
    expect(screen.getByText("87.5%")).toBeTruthy();
    expect(screen.getByText("Supported by")).toBeTruthy();
    expect(screen.getByText("2 pages")).toBeTruthy();
  });

  it("evidence is collapsed by default but the real retrieved passage text and source URL are one click away, never hidden entirely", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));

    // collapsed by default: the URL isn't on screen until the evidence toggle is opened
    expect(screen.queryByText(/https:\/\/acme.example\/faq/)).toBeNull();
    fireEvent.click(screen.getByText(/View evidence/));

    expect(screen.getAllByText(/https:\/\/acme.example\/pricing/).length).toBeGreaterThan(0);
    expect(screen.getByText(/https:\/\/acme.example\/faq/)).toBeTruthy();
    expect(screen.getByText(/Pricing starts at \$10\/month/)).toBeTruthy();
  });

  it("Free plan shows a real 2-item evidence preview plus a Pro unlock affordance, never a full blur", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_LOCKED_PREVIEW], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What is AI attendance?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Partially supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Partially supported").closest(".au-as-card-head"));

    // the 2 real preview items ARE rendered (never a client-only blur)
    expect(screen.getByText(/AI attendance tracks presence\./)).toBeTruthy();
    expect(screen.getByText(/3 more sources — Pro/)).toBeTruthy();
  });

  it("Pro plan (full_evidence unlocked) sees no locked-source affordance", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));
    expect(screen.queryByText(/more sources — Pro/)).toBeNull();
  });

  it("off-topic question renders INSUFFICIENT_EVIDENCE with a low topic-alignment %, not a fabricated content opportunity", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_OFF_TOPIC], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"),
      { target: { value: "What is the best chocolate lava cake recipe?" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());

    fireEvent.click(screen.getByText("Not enough evidence").closest(".au-as-card-head"));
    expect(screen.getByText("4.2%")).toBeTruthy();   // Topic alignment metric
    expect(screen.getByText("0 pages")).toBeTruthy();
    const gapText = screen.getByText(/This topic is not currently supported/).textContent.toLowerCase();
    expect(gapText).not.toMatch(/add more content|create a page|opportunity/);
  });

  it("LOW state shows distinct copy from INSUFFICIENT_EVIDENCE and still surfaces real (weak) evidence", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_LOW], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"),
      { target: { value: "How much does attendance software cost per month?" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not supported")).toBeTruthy());

    fireEvent.click(screen.getByText("Not supported").closest(".au-as-card-head"));
    expect(screen.getByText(/We found some relevant information, but the website does not/)).toBeTruthy();
    fireEvent.click(screen.getByText(/View evidence/));
    expect(screen.getAllByText(/Attendance software/).length).toBeGreaterThan(0);
    // never the insufficient-evidence copy
    expect(screen.queryByText(/We couldn't find meaningful evidence/)).toBeNull();
  });

  it("HIGH/MEDIUM relevant questions render the topic alignment metric alongside evidence relevance", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));

    expect(screen.getByText("Topic alignment")).toBeTruthy();
    expect(screen.getByText("82.4%")).toBeTruthy();
    expect(screen.getByText("Evidence relevance")).toBeTruthy();
    expect(screen.getByText("91.2%")).toBeTruthy();
  });

  it("zero relevant pages is shown for an insufficient-evidence result", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_INSUFFICIENT], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"), { target: { value: "spacecraft propulsion question" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());
    fireEvent.click(screen.getByText("Not enough evidence").closest(".au-as-card-head"));
    expect(screen.getByText("0 pages")).toBeTruthy();
  });

  it("off-topic questions never auto-trigger the LLM — Explain why still requires an explicit click", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_OFF_TOPIC], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"),
      { target: { value: "What is the best chocolate lava cake recipe?" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());
    // runAnswerSimulation itself never requests the LLM step unless the checkbox was ticked
    const lastCall = runAnswerSimulation.mock.calls[runAnswerSimulation.mock.calls.length - 1];
    const [, payload] = lastCall;
    expect(payload.requestLlmStep).toBe(false);
  });

  it("Phase F: Support text is a real, complete sentence built from the site's own evidence — never the incomplete lead-in fragment alone", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));

    // never just the bare backend lead-in ("Based on the website's currently scanned
    // content:") with no actual content after it
    expect(screen.queryByText("Based on the website's currently scanned content:")).toBeNull();
    const support = document.querySelector(".au-as-support");
    expect(support.textContent).toContain("We offer Basic and Pro plans.");
    expect(support.textContent).toMatch(/scanned content/i);
  });

  it("Phase F: a fully-supported question with no content gap shows an explicit 'No fix needed', never an empty gap", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));

    expect(screen.getByText(/No fix needed/)).toBeTruthy();
    expect(screen.queryByText("What to fix")).toBeNull();
  });

  it("Phase F: a content gap shows both What to fix and a concrete Fix, not just a raw missing-info list", async () => {
    getSimulatorQuestions.mockResolvedValue({ ...QUESTIONS, bank_questions: [] });
    runAnswerSimulation.mockResolvedValue({
      run_id: "r1", status: "completed", results: [RESULT_INSUFFICIENT], locked_count: 0,
    });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(getSimulatorQuestions).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText("Add a custom question…"), { target: { value: "spacecraft propulsion question" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());
    fireEvent.click(screen.getByText("Not enough evidence").closest(".au-as-card-head"));

    expect(screen.getByText("What to fix")).toBeTruthy();
    expect(screen.getByText(/This topic is not currently supported/)).toBeTruthy();
    expect(screen.getByText("Fix")).toBeTruthy();
    expect(screen.getByText(/Add a section that directly addresses this question/)).toBeTruthy();
  });

  it("existing relevant-question UI (evidence, brand mention, answer text) is unchanged by the off-topic feature", async () => {
    getSimulatorQuestions.mockResolvedValue(QUESTIONS);
    runAnswerSimulation.mockResolvedValue({ run_id: "r1", status: "completed", results: [RESULT_HIGH], locked_count: 0 });
    render(<AnswerSimulator monitorId="m1" canRun={true} />);
    await waitFor(() => expect(screen.getByText("What pricing plans are available?")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByText("Run Simulation"));
    await waitFor(() => expect(screen.getByText("Supported")).toBeTruthy());
    fireEvent.click(screen.getByText("Supported").closest(".au-as-card-head"));
    expect(screen.getByText("YES")).toBeTruthy();
    expect(screen.getAllByText(/We offer Basic and Pro plans/).length).toBeGreaterThan(0);
  });
});
