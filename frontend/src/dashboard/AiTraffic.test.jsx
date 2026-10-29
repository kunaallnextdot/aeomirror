/* AI Traffic (GA4 connection + traffic dashboard) — pure rendering + interaction
   tests, api.js mocked at the boundary (same convention as AnswerSimulator.test.jsx /
   QuestionBank.test.jsx). No network. Note: this codebase has no @testing-library/
   jest-dom, so assertions use getByText (throws if absent) / queryByText + toBeNull
   (presence/absence), never .toBeInTheDocument(). */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

// jsdom has no ResizeObserver; the Trends chart's recharts ResponsiveContainer
// always mounts once "active + synced" renders (see BusinessImpact.test.jsx for
// the same stub).
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

vi.mock("../api.js", () => ({
  getGa4Status: vi.fn(),
  getGa4Traffic: vi.fn(),
  getGa4Overview: vi.fn(),
  getGa4Trends: vi.fn(),
  getGa4Channels: vi.fn(),
  getGa4LandingPages: vi.fn(),
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
  getGa4Status, getGa4Traffic, getGa4Overview, getGa4Trends, getGa4Channels,
  getGa4LandingPages, connectGoogleAnalytics, listGa4Properties,
  selectGa4Property, triggerGa4Sync, disconnectGoogleAnalytics, ScanError,
} from "../api.js";
import AiTraffic from "./AiTraffic.jsx";

const ZERO_METRICS = {
  total_users: 0, new_users: 0, sessions: 0, engaged_sessions: 0, views: 0,
  engagement_duration_sec: 0, key_events: 0, returning_users: 0,
  engagement_rate: null, avg_engagement_time_sec: null, conversion_rate: null,
};

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
  // Sensible defaults for any test that reaches "active + synced" without caring
  // about dashboard content specifically — overridden per-test where it matters.
  getGa4Overview.mockResolvedValue({
    date_range: { start: "2026-01-01", end: "2026-01-28" },
    previous_date_range: { start: "2025-12-04", end: "2025-12-31" },
    current: ZERO_METRICS, previous: ZERO_METRICS, key_events_configured: null,
  });
  getGa4Trends.mockResolvedValue({ date_range: { start: "2026-01-01", end: "2026-01-28" }, daily: [] });
  getGa4Channels.mockResolvedValue({ channels: [], total: 0, page: 1, page_size: 25 });
  getGa4LandingPages.mockResolvedValue({ pages: [], total: 0, page: 1, page_size: 25 });
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

  it("clicking Connect Google Analytics opens the consent modal first, WITHOUT calling the backend yet", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    await waitFor(() => screen.getByText(/connect google analytics/i));
    fireEvent.click(screen.getByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    screen.getByText(/what you'll get/i);
    screen.getByText(/what data will aeomirror access\?/i);
    screen.getByText(/your control and privacy/i);
    expect(connectGoogleAnalytics).not.toHaveBeenCalled();
  });

  it("the consent modal shows the exact requested scope in readable language", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    screen.getByText("https://www.googleapis.com/auth/analytics.readonly");
    screen.getByText(/view your google analytics data\./i);
  });

  it("the consent modal links to the real, public Privacy Policy and Terms of Service", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    expect(screen.getByRole("link", { name: /privacy policy/i }).getAttribute("href")).toBe("/privacy");
    expect(screen.getByRole("link", { name: /terms of service/i }).getAttribute("href")).toBe("/terms");
  });

  it("cancelling the consent modal closes it and never initiates OAuth", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByText(/^cancel$/i));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(connectGoogleAnalytics).not.toHaveBeenCalled();
  });

  it("the continue button is disabled until the acknowledgement checkbox is checked", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    const confirmBtn = screen.getByRole("button", { name: /agree & continue to google/i });
    expect(confirmBtn.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(confirmBtn.disabled).toBe(false);
  });

  it("agreeing and continuing calls the real OAuth-connect endpoint and navigates the real browser to the authorize_url returned", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    connectGoogleAnalytics.mockResolvedValue({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /agree & continue to google/i }));
    await waitFor(() => expect(connectGoogleAnalytics).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/v2/auth?x=1"));
  });

  it("prevents duplicate submissions: the continue button disables itself while the request is in flight", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    let resolveConnect;
    connectGoogleAnalytics.mockReturnValue(new Promise((res) => { resolveConnect = res; }));
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("checkbox"));
    const confirmBtn = screen.getByRole("button", { name: /redirecting|agree & continue to google/i });
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(screen.getByRole("button", { name: /redirecting/i }).disabled).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /redirecting/i }));   // a second click while busy
    expect(connectGoogleAnalytics).toHaveBeenCalledTimes(1);   // never double-fired
    resolveConnect({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
  });

  it("an OAuth-initiation failure shows an actionable error inside the modal with a Retry action, never a fake success", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    connectGoogleAnalytics.mockRejectedValueOnce(new ScanError("Could not start the connection.", 500));
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /agree & continue to google/i }));
    await waitFor(() => screen.getByText("Could not start the connection."));
    expect(assignSpy).not.toHaveBeenCalled();
    // Retry re-attempts the same call, and this time it succeeds.
    connectGoogleAnalytics.mockResolvedValueOnce({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=2" });
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/v2/auth?x=2"));
  });

  it("an invalid (empty) authorization URL from the backend is treated as an error, never followed", async () => {
    getGa4Status.mockResolvedValue({ status: "not_connected" });
    connectGoogleAnalytics.mockResolvedValue({ authorize_url: "" });
    renderAt();
    fireEvent.click(await screen.findByText(/connect google analytics/i));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /agree & continue to google/i }));
    await waitFor(() => screen.getByText(/could not start the connection/i));
    expect(assignSpy).not.toHaveBeenCalled();
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

  it("pending_property + zero accessible properties: shows actionable guidance and a way to try a different Google account, not a dead end", async () => {
    // This is the exact production symptom under investigation: OAuth succeeds,
    // but the connected Google account has no accessible GA4 property.
    getGa4Status.mockResolvedValue({ status: "pending_property" });
    listGa4Properties.mockResolvedValue({ properties: [] });
    connectGoogleAnalytics.mockResolvedValue({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    renderAt();
    await waitFor(() => screen.getByText(/load properties/i));
    fireEvent.click(screen.getByText(/load properties/i));
    await waitFor(() => screen.getByText(/no ga4 properties are accessible/i));

    const retryButton = screen.getByRole("button", { name: /try a different google account/i });
    fireEvent.click(retryButton);
    await waitFor(() => expect(connectGoogleAnalytics).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith(
      "https://accounts.google.com/o/oauth2/v2/auth?x=1"));
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
      by_source_detail: {
        chatgpt: { sessions: 42, new_users: 10, engaged_sessions: 30, views: 80, key_events: 2, engagement_rate: 0.7, conversion_rate: 0.05 },
        perplexity: { sessions: 7, new_users: 2, engaged_sessions: 4, views: 10, key_events: 0, engagement_rate: 0.57, conversion_rate: 0 },
        gemini: { sessions: 0, new_users: 0, engaged_sessions: 0, views: 0, key_events: 0, engagement_rate: null, conversion_rate: null },
        copilot: { sessions: 0, new_users: 0, engaged_sessions: 0, views: 0, key_events: 0, engagement_rate: null, conversion_rate: null },
        claude: { sessions: 3, new_users: 1, engaged_sessions: 2, views: 5, key_events: 0, engagement_rate: 0.67, conversion_rate: 0 },
        other_identifiable: { sessions: 11, new_users: 3, engaged_sessions: 6, views: 15, key_events: 0, engagement_rate: 0.55, conversion_rate: 0 },
        unattributable: { sessions: 500, new_users: 100, engaged_sessions: 200, views: 900, key_events: 10, engagement_rate: 0.4, conversion_rate: 0.02 },
      },
    });
    renderAt();
    await waitFor(() => screen.getByText(/ai traffic intelligence/i));
    screen.getByText(/42 sessions/i);
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
