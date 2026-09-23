/* Regression tests for a real cross-site data leak: an org with exactly one
   pre-existing monitor (e.g. thedocmirror.com) was silently auto-navigated into that
   monitor's Answer Tracking data whenever the user opened the generic "Answer
   Tracking" sidebar link — even after scanning a brand-new, not-yet-monitored site
   (e.g. webpulseindia.com). The fix: auto-select a monitor only when its domain
   actually matches the current scan's domain; otherwise show an honest "no site set
   up yet" state instead of defaulting into someone else's data. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
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

const DOCMIRROR = { id: "mon-docmirror", name: "DocMirror", domain: "thedocmirror.com", normalized_url: "thedocmirror.com" };
const WEBPULSE = { id: "mon-webpulse", name: "Webpulse", domain: "webpulseindia.com", normalized_url: "webpulseindia.com" };

// A route wrapper is needed (not a bare static-prop render) so that AnswerTracking's
// own internal navigate() calls (the auto-select redirect under test) actually cause
// a re-render with a fresh selectedMonitorId — exactly like the real
// AnswerTrackingRoute in app/routes.jsx, which re-derives it from useParams().
// currentScanDomain is passed straight through (in the real app it comes from
// already-loaded dashboard context, not the URL, so it's fine to hold it fixed here).
function RouteAware({ currentScanDomain }) {
  const { monitorId, runId } = useParams();
  return <AnswerTracking selectedMonitorId={monitorId || null} selectedRunId={runId || null}
                         currentScanDomain={currentScanDomain} />;
}

function renderAT({ selectedMonitorId, currentScanDomain = null } = {}) {
  const initial = selectedMonitorId
    ? `/app/answer-tracking/${selectedMonitorId}` : "/app/answer-tracking";
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/app/answer-tracking" element={<RouteAware currentScanDomain={currentScanDomain} />} />
        <Route path="/app/answer-tracking/:monitorId" element={<RouteAware currentScanDomain={currentScanDomain} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AnswerTracking — cross-site isolation (bug fix regression)", () => {
  it("TEST 1: a sole pre-existing monitor for a DIFFERENT site is never auto-selected for the current scan's domain — no DocMirror data ever loads", async () => {
    listMonitors.mockResolvedValue({ monitors: [DOCMIRROR] });
    renderAT({ currentScanDomain: "webpulseindia.com" });

    await waitFor(() => expect(screen.getByText(/No Answer Tracking site is set up yet/)).toBeTruthy());
    expect(screen.getByText("webpulseindia.com")).toBeTruthy();
    // never silently redirected into the docmirror monitor
    expect(getMonitorAnswerTracking).not.toHaveBeenCalled();
    expect(screen.queryByTestId("answer-simulator")).toBeNull();
  });

  it("still auto-selects the sole monitor when its domain DOES match the current scan", async () => {
    listMonitors.mockResolvedValue({ monitors: [WEBPULSE] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Webpulse", site_url: "webpulseindia.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ currentScanDomain: "webpulseindia.com" });

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    expect(getMonitorAnswerTracking).toHaveBeenCalledWith("mon-webpulse");
  });

  it("preserves the original single-monitor convenience when the current scan's domain is unknown", async () => {
    listMonitors.mockResolvedValue({ monitors: [DOCMIRROR] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "DocMirror", site_url: "thedocmirror.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ currentScanDomain: null });   // e.g. org has no scans at all yet

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    expect(getMonitorAnswerTracking).toHaveBeenCalledWith("mon-docmirror");
  });

  it("with multiple monitors, auto-selects specifically the one matching the current scan's domain, never just the first", async () => {
    getMonitorAnswerTracking.mockClear();   // isolate from other tests' call history in this shared mock
    listMonitors.mockResolvedValue({ monitors: [DOCMIRROR, WEBPULSE] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Webpulse", site_url: "webpulseindia.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ currentScanDomain: "webpulseindia.com" });

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    expect(getMonitorAnswerTracking).toHaveBeenCalledWith("mon-webpulse");
    expect(getMonitorAnswerTracking).not.toHaveBeenCalledWith("mon-docmirror");
  });

  it("compact context indicator shows the active site's real domain, not a fabricated label", async () => {
    listMonitors.mockResolvedValue({ monitors: [WEBPULSE] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Webpulse", site_url: "webpulseindia.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ currentScanDomain: "webpulseindia.com" });

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    const indicator = document.querySelector(".au-at-context");
    expect(indicator.textContent).toContain("webpulseindia.com");
  });

  it("an explicit selectedMonitorId in the URL always wins — no auto-select logic involved on reload — but a real domain mismatch is shown, non-blocking", async () => {
    listMonitors.mockResolvedValue({ monitors: [DOCMIRROR, WEBPULSE] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Webpulse", site_url: "webpulseindia.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ selectedMonitorId: "mon-webpulse", currentScanDomain: "thedocmirror.com" });

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    // even though currentScanDomain points elsewhere, an explicit id in the URL is authoritative
    expect(getMonitorAnswerTracking).toHaveBeenCalledWith("mon-webpulse");
    // Phase H: the mismatch is surfaced, never silently invisible, but never blocks the user
    const warning = document.querySelector(".au-at-mismatch");
    expect(warning.textContent).toContain("webpulseindia.com");
    expect(warning.textContent).toContain("thedocmirror.com");
  });

  it("no mismatch warning when the explicitly-selected monitor's domain matches the current scan", async () => {
    listMonitors.mockResolvedValue({ monitors: [WEBPULSE] });
    getMonitorAnswerTracking.mockResolvedValue({
      site_name: "Webpulse", site_url: "webpulseindia.com", prompts: [], max_prompts: 10,
      runs: [], estimate: { call_count: 0, providers: [] },
    });
    renderAT({ selectedMonitorId: "mon-webpulse", currentScanDomain: "webpulseindia.com" });

    await waitFor(() => expect(screen.getByTestId("answer-simulator")).toBeTruthy());
    expect(document.querySelector(".au-at-mismatch")).toBeNull();
  });
});
