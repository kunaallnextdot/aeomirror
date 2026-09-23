/* AEO Answer Simulator is the PRIMARY panel; the existing live-provider flow is
   demoted to a collapsed, clearly-labeled "Provider Tracking (Premium)" section.
   AnswerSimulator.jsx itself is mocked here (its own rendering is covered by
   AnswerSimulator.test.jsx) — this file only asserts the placement/collapse. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  listMonitors: vi.fn(),
  getMonitorAnswerTracking: vi.fn(),
  addMonitorPrompt: vi.fn(),
  runMonitorAnswerTracking: vi.fn(),
  getMonitorAnswerTrackingTrend: vi.fn(),
  updatePrompt: vi.fn(),
  deletePrompt: vi.fn(),
  getPromptRun: vi.fn(),
  getPromptRunSummary: vi.fn(),
  getPromptRunResults: vi.fn(),
  ScanError: class ScanError extends Error {},
}));
vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));
vi.mock("./AnswerSimulator.jsx", () => ({
  default: (props) => <div data-testid="answer-simulator">Simulator for {props.monitorId}</div>,
}));

import { listMonitors, getMonitorAnswerTracking } from "../api.js";
import AnswerTracking from "./AnswerTracking.jsx";

describe("AnswerTracking primary/secondary panel placement", () => {
  it("mounts the AEO Answer Simulator as primary and collapses Provider Tracking behind a details toggle", async () => {
    listMonitors.mockResolvedValue({ monitors: [{ id: "m1", name: "Acme", normalized_url: "acme.example" }] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Acme", site_url: "acme.example", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });

    render(
      <MemoryRouter initialEntries={["/app/answer-tracking/m1"]}>
        <AnswerTracking selectedMonitorId="m1" selectedRunId={null} />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());

    const details = document.querySelector("details.au-at-premium");
    expect(details).toBeTruthy();
    expect(details.open).toBe(false);   // collapsed by default
    expect(details.querySelector("summary").textContent).toBe("Provider Tracking (Premium)");

    // The simulator's DOM position comes before the collapsed provider-tracking details.
    const simulatorEl = screen.getByTestId("answer-simulator");
    expect(
      simulatorEl.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("explainer copy describes the simulator as the primary, zero-cost flow", async () => {
    listMonitors.mockResolvedValue({ monitors: [{ id: "m1", name: "Acme", normalized_url: "acme.example" }] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Acme", site_url: "acme.example", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    render(
      <MemoryRouter initialEntries={["/app/answer-tracking/m1"]}>
        <AnswerTracking selectedMonitorId="m1" selectedRunId={null} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/AEO Answer Simulator/)).toBeTruthy());
    expect(screen.getByText(/no external AI calls by default/)).toBeTruthy();
  });
});
