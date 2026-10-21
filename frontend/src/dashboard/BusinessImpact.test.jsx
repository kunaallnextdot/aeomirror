/* AI Business Impact — pure rendering + interaction tests, api.js mocked at the
   boundary (same convention as AiTraffic.test.jsx). No network. */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  listMonitors: vi.fn(),
  getTrackingStatus: vi.fn(),
  generateTrackingApiKey: vi.fn(),
  getBusinessImpactSummary: vi.fn(),
  getBusinessImpactTrafficOverTime: vi.fn(),
  getBusinessImpactChannels: vi.fn(),
  getBusinessImpactLandingPages: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("./UpgradeModal.jsx", () => ({
  useUpgrade: () => ({ openUpgrade: vi.fn() }),
}));

import {
  listMonitors, getTrackingStatus, getBusinessImpactSummary, getBusinessImpactTrafficOverTime,
  getBusinessImpactChannels, getBusinessImpactLandingPages,
} from "../api.js";
import BusinessImpact from "./BusinessImpact.jsx";

const MONITOR = { id: "m1", name: "Acme", normalized_url: "acme.example" };

beforeEach(() => {
  vi.clearAllMocks();
  getBusinessImpactTrafficOverTime.mockResolvedValue({ series: [] });
  getBusinessImpactChannels.mockResolvedValue({ channels: [] });
  getBusinessImpactLandingPages.mockResolvedValue({ pages: [] });
});

describe("BusinessImpact", () => {
  it("no sites yet: prompts to add a site, never shows a broken dashboard", async () => {
    listMonitors.mockResolvedValue({ monitors: [] });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText(/add a site under monitoring first/i));
  });

  it("tracking not installed: shows the setup wizard with the real snippet, not a placeholder", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({
      site_id: "m1", installed: false,
      snippet: '<script defer src="https://api.example.com/t/tracker.js" data-site-id="m1"></script>',
      collect_url: "https://api.example.com/t/collect", has_tracking_api_key: false,
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText(/install ai business impact tracking/i));
    expect(screen.getByText(/data-site-id="m1"/)).toBeTruthy();
    // Never blocks other AEO features — explicitly stated in the wizard copy.
    screen.getByText(/never blocks those/i);
  });

  it("installed but zero sessions in range: an honest 'no data yet' state, not misleading zeros", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 0, ai_sessions: 0, ai_share_pct: null, ai_conversions: 0,
      ai_conversion_rate_pct: null, ai_revenue_cents: 0, confirmed_bookings: 0, leads: 0,
      has_data: false, is_pro: true, ga4_connected: false,
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText(/no visits have been recorded yet/i));
    expect(screen.queryByText(/website sessions/i)).toBeNull();
  });

  it("renders real summary data, and locks conversion/revenue cards on free plan", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 420, ai_sessions: 84, ai_share_pct: 20.0,
      ai_conversions: null, ai_conversion_rate_pct: null, ai_revenue_cents: null,
      confirmed_bookings: null, leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText("420"));
    screen.getByText("84");
    // Locked fields show a Pro badge, never a fabricated number.
    const lockButtons = screen.getAllByText(/^Pro$/);
    expect(lockButtons.length).toBeGreaterThan(0);
  });

  it("Pro plan: renders conversions and revenue as real numbers, not locked", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 100, ai_sessions: 40, ai_share_pct: 40.0,
      ai_conversions: 5, ai_conversion_rate_pct: 12.5, ai_revenue_cents: 25000,
      confirmed_bookings: 2, leads: 3, has_data: true, is_pro: true, ga4_connected: false,
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText("$250.00"));
    screen.getByText("5");
  });

  it("date-range filter re-fetches data for the new range", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 10, ai_sessions: 2, ai_share_pct: 20, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText("10"));
    const callsBefore = getBusinessImpactSummary.mock.calls.length;
    fireEvent.click(screen.getByText("Last 7 days"));
    await waitFor(() => expect(getBusinessImpactSummary.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it("never fabricates a landing-page revenue estimate in the rendered UI", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 100, ai_sessions: 40, ai_share_pct: 40, ai_conversions: 1,
      ai_conversion_rate_pct: 2.5, ai_revenue_cents: 1000, confirmed_bookings: 0, leads: 0,
      has_data: true, is_pro: true, ga4_connected: false,
    });
    getBusinessImpactLandingPages.mockResolvedValue({
      pages: [{ url: "https://acme.example/pricing", sessions: 30, ai_sessions: 10, conversions: 0,
               conversion_rate_pct: 0, revenue_cents: 0, confidence: "high", pattern: "steady",
               priority: "low", recommendation: "No urgent pattern detected.",
               reason: "30 sessions, 0% conversion rate, 10 AI-referred.", aeo: null }],
    });
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText(/pages worth improving/i));
    // The page explicitly DISCLAIMS estimating revenue (legitimate, expected copy) —
    // what must never appear is an actual fabricated estimate VALUE being presented.
    expect(screen.queryByText(/estimated revenue:\s*\$/i)).toBeNull();
    expect(screen.queryByText(/projected revenue/i)).toBeNull();
    screen.getByText(/never a guaranteed outcome/i);
    screen.getByText(/never an estimated revenue gain/i);
  });

  it("a backend error renders a real error message, not a silent blank page", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockRejectedValue(new (await import("../api.js")).ScanError("Could not reach the server.", "network"));
    render(<BusinessImpact />);
    await waitFor(() => screen.getByText("Could not reach the server."));
  });
});
