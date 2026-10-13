/* AI Traffic (GA4 connection + traffic dashboard) — pure rendering + interaction
   tests, api.js mocked at the boundary (same convention as AnswerSimulator.test.jsx /
   QuestionBank.test.jsx). No network. Note: this codebase has no @testing-library/
   jest-dom, so assertions use getByText (throws if absent) / queryByText + toBeNull
   (presence/absence), never .toBeInTheDocument(). */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  getGa4Status: vi.fn(),
  getGa4Traffic: vi.fn(),
  connectGoogleAnalytics: vi.fn(),
  listGa4Properties: vi.fn(),
  selectGa4Property: vi.fn(),
  triggerGa4Sync: vi.fn(),
  disconnectGoogleAnalytics: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));
vi.mock("./UpgradeModal.jsx", () => ({
  useUpgrade: () => ({ openUpgrade: vi.fn() }),
}));

import {
  getGa4Status, getGa4Traffic, connectGoogleAnalytics, listGa4Properties,
  selectGa4Property, triggerGa4Sync, disconnectGoogleAnalytics, ScanError,
} from "../api.js";
import AiTraffic from "./AiTraffic.jsx";

function renderAt(path = "/app/analytics/ai-traffic") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/app/analytics/ai-traffic" element={<AiTraffic />} /></Routes>
    </MemoryRouter>
  );
}

const assignSpy = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  assignSpy.mockClear();
  window.confirm = vi.fn(() => true);
  // jsdom's window.location is a non-configurable property (can't vi.spyOn it
  // directly) — redefine it on the object itself rather than reassigning `window.
  // location`, which jsdom also rejects.
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign: assignSpy }, writable: true, configurable: true,
  });
});

describe("AiTraffic", () => {
  it("not connected: shows a Connect button, not a traffic dashboard", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    await waitFor(() => screen.getByText(/connect google analytics/i));
    expect(screen.queryByText(/sessions by source/i)).toBeNull();
  });

  it("connect() navigates the real browser to the authorize_url, not just a fetch", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    connectGoogleAnalytics.mockResolvedValue({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    renderAt();
    await waitFor(() => screen.getByText(/connect google analytics/i));
    fireEvent.click(screen.getByText(/connect google analytics/i));
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/v2/auth?x=1"));
  });

  it("shows a success banner after a successful OAuth redirect (?ga4=connected)", async () => {
    getGa4Status.mockResolvedValue({ status: "pending_property" });
    renderAt("/app/analytics/ai-traffic?ga4=connected");
    await waitFor(() => screen.getByText(/google analytics connected/i));
  });

  it("shows a mapped, non-technical error banner after a failed OAuth redirect", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt("/app/analytics/ai-traffic?ga4=error&reason=invalid_state");
    await waitFor(() => screen.getByText(/expired or was already used/i));
  });

  it("pending_property: lets the user load and pick a GA4 property", async () => {
    getGa4Status.mockResolvedValue({ status: "pending_property" });
    listGa4Properties.mockResolvedValue({
      properties: [{ property_id: "111", property_name: "Acme Main", account_name: "Acme" }],
    });
    selectGa4Property.mockResolvedValue({ status: "ok" });
    renderAt();
    await waitFor(() => screen.getByText(/load properties/i));
    fireEvent.click(screen.getByText(/load properties/i));
    await waitFor(() => screen.getByText("Acme Main"));

    getGa4Status.mockResolvedValue({ status: "active", ga4_property_name: "Acme Main", last_synced_at: null });
    getGa4Traffic.mockResolvedValue({ synced: false, last_synced_at: null, by_source: {} });
    fireEvent.click(screen.getByText("Acme Main"));
    await waitFor(() => expect(selectGa4Property).toHaveBeenCalledWith("111"));
  });

  it("active but not yet synced: shows a pending message, never fake zero numbers", async () => {
    getGa4Status.mockResolvedValue({
      status: "active", ga4_property_name: "Acme Main", last_synced_at: null,
    });
    getGa4Traffic.mockResolvedValue({ synced: false, last_synced_at: null, by_source: {} });
    renderAt();
    await waitFor(() => screen.getByText(/sync pending/i));
    expect(screen.queryByText(/sessions by source/i)).toBeNull();
  });

  it("active + synced: renders the real by-source breakdown from the API, not invented data", async () => {
    getGa4Status.mockResolvedValue({
      status: "active", ga4_property_name: "Acme Main", last_synced_at: "2026-10-12T10:00:00Z",
    });
    getGa4Traffic.mockResolvedValue({
      synced: true, last_synced_at: "2026-10-12T10:00:00Z",
      by_source: { chatgpt: 42, perplexity: 7, gemini: 0, copilot: 0, claude: 3,
                  other_identifiable: 11, unattributable: 500 },
    });
    renderAt();
    await waitFor(() => screen.getByText(/sessions by source/i));
    screen.getByText("42");
    screen.getByText("500");
    // The honest-limitation note is always shown alongside real numbers.
    screen.getByText(/ai overview clicks cannot currently be separated/i);
  });

  it("sync now triggers a manual sync and shows a confirmation banner", async () => {
    getGa4Status.mockResolvedValue({
      status: "active", ga4_property_name: "Acme Main", last_synced_at: "2026-10-12T10:00:00Z",
    });
    getGa4Traffic.mockResolvedValue({ synced: true, last_synced_at: "2026-10-12T10:00:00Z", by_source: { chatgpt: 1 } });
    triggerGa4Sync.mockResolvedValue({ status: "queued" });
    renderAt();
    await waitFor(() => screen.getByText(/sync now/i));
    fireEvent.click(screen.getByText(/sync now/i));
    await waitFor(() => expect(triggerGa4Sync).toHaveBeenCalled());
    await waitFor(() => screen.getByText(/sync started/i));
  });

  it("revoked (a real backend status after a prior disconnect, e.g. on page reload): shows a distinct reconnect-required state, not a blank card", async () => {
    // The connection row is never deleted on disconnect — /status can genuinely
    // return "revoked" on a fresh page load (not just the component's own
    // optimistic post-click state). No branch previously existed for this value.
    getGa4Status.mockResolvedValue({ status: "revoked", ga4_property_name: "Acme Main" });
    renderAt();
    await waitFor(() => screen.getByText(/disconnected and is no longer syncing/i));
    screen.getByRole("button", { name: /connect google analytics/i });
    expect(screen.queryByText(/sessions by source/i)).toBeNull();
  });

  it("disconnect asks for confirmation before calling the API", async () => {
    getGa4Status.mockResolvedValue({
      status: "active", ga4_property_name: "Acme Main", last_synced_at: "2026-10-12T10:00:00Z",
    });
    getGa4Traffic.mockResolvedValue({ synced: true, last_synced_at: "2026-10-12T10:00:00Z", by_source: {} });
    disconnectGoogleAnalytics.mockResolvedValue({ status: "disconnected" });
    renderAt();
    await waitFor(() => screen.getByText(/disconnect/i));
    fireEvent.click(screen.getByText(/disconnect/i));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(disconnectGoogleAnalytics).toHaveBeenCalled());
  });

  it("platform not configured: shows a setup-unavailable state, never a Connect button or scanner error", async () => {
    getGa4Status.mockResolvedValue({ status: "not_configured" });
    renderAt();
    await waitFor(() => screen.getByText(/has(n't| not) completed its integration setup/i));
    expect(screen.queryByText(/connect google analytics/i)).toBeNull();
    expect(screen.queryByText(/pro feature/i)).toBeNull();
    expect(screen.queryByText(/scanner/i)).toBeNull();
  });

  it("not Pro: shows an upgrade prompt instead of the connect flow", async () => {
    getGa4Status.mockResolvedValue({ status: "not_available", reason: "upgrade_required" });
    renderAt();
    await waitFor(() => screen.getByText(/pro feature/i));
    expect(screen.queryByText(/connect google analytics/i)).toBeNull();
  });

  it("error status: shows the mapped error and a reconnect option", async () => {
    getGa4Status.mockResolvedValue({
      status: "error", last_sync_error_code: "auth_revoked", ga4_property_name: "Acme Main",
    });
    renderAt();
    await waitFor(() => screen.getByText(/access was revoked or expired/i));
    screen.getByRole("button", { name: /reconnect/i });
  });

  it("a generic API failure renders the real error, not a silent blank page", async () => {
    getGa4Status.mockRejectedValue(new ScanError("Could not reach the server.", "network"));
    renderAt();
    await waitFor(() => screen.getByText("Could not reach the server."));
  });
});
