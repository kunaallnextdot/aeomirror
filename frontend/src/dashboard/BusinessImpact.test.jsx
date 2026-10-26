/* AI Business Impact — pure rendering + interaction tests, api.js mocked at the
   boundary (same convention as AiTraffic.test.jsx). No network. */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText(/add a site under monitoring first/i));
  });

  it("tracking not installed: shows the setup wizard with the real snippet, not a placeholder", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({
      site_id: "m1", installed: false,
      snippet: '<script defer src="https://api.example.com/t/tracker.js" data-site-id="m1"></script>',
      collect_url: "https://api.example.com/t/collect", has_tracking_api_key: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("$250.00"));
    screen.getByText("5");
  });

  it("Pro plan with zero AI-referred sessions: AI conversion rate is NOT locked behind a false Pro badge", async () => {
    // The actual regression: ai_conversion_rate_pct is null both when the account
    // isn't Pro AND when a genuinely-Pro account simply has zero AI-referred
    // sessions in this range — `locked` must key off `is_pro`, never off the raw
    // value, or a real Pro account sees a false upgrade prompt for this one card.
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 50, ai_sessions: 0, ai_share_pct: 0,
      ai_conversions: 0, ai_conversion_rate_pct: null, ai_revenue_cents: 0,
      confirmed_bookings: 0, leads: 0, has_data: true, is_pro: true, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("Not enough data yet"));
    // No Pro lock badge anywhere on this (fully Pro, just data-sparse) account.
    expect(screen.queryByText(/^Pro$/)).toBeNull();
  });

  it("shows when the last event was received, and an honest 'none yet' when there isn't one", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({
      site_id: "m1", installed: true, status: "active", has_tracking_api_key: false,
      last_event_at: "2026-10-26T05:42:00Z",
    });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 10, ai_sessions: 2, ai_share_pct: 20, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText(/Last event received: /));
    expect(screen.queryByText(/Last event received: none yet/)).toBeNull();
  });

  it("shows a clear empty-state note for the traffic chart instead of silently rendering nothing", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 5, ai_sessions: 1, ai_share_pct: 20, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    getBusinessImpactTrafficOverTime.mockResolvedValue({ series: [] });   // real data exists, but none for the chart
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText(/Not enough daily traffic yet to chart a trend/));
    screen.getByText(/not necessarily that the site received no traffic/);
  });

  it("renders page views and unique visitors as their own real numbers", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 100, total_page_views: 340, unique_visitors: 72,
      ai_sessions: 40, ai_unique_visitors: 28, ai_share_pct: 40.0,
      ai_conversions: null, ai_conversion_rate_pct: null, ai_revenue_cents: null,
      confirmed_bookings: null, leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("340"));
    screen.getByText("72");
    screen.getByText("28");
  });

  it("links to the GA4 AI Traffic page when GA4 is also connected, without blending the numbers", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 10, ai_sessions: 2, ai_share_pct: 20, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: true,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("10"));
    const link = screen.getByRole("link", { name: /AI Traffic/ });
    expect(link.getAttribute("href")).toBe("/app/analytics/ai-traffic");
  });

  it("shows unverified leads/bookings as a separate sub-label, never folded into the confirmed count", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 100, ai_sessions: 40, ai_share_pct: 40.0,
      ai_conversions: 5, ai_conversion_rate_pct: 12.5, ai_revenue_cents: 25000,
      confirmed_bookings: 2, unverified_bookings: 3, leads: 1, unverified_leads: 4,
      has_data: true, is_pro: true, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("2"));   // confirmed_bookings stays 2, not 5
    screen.getByText("1");                        // leads stays 1, not 5
    screen.getByText(/\+3 unverified/);
    screen.getByText(/\+4 unverified/);
  });

  it("shows a stale-install notice when status is installed_inactive, without claiming it's an error", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({
      site_id: "m1", installed: true, status: "installed_inactive", has_tracking_api_key: false,
    });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 50, ai_sessions: 5, ai_share_pct: 10, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText(/no new visits recorded in over a week/i));
  });

  it("date-range filter re-fetches data for the new range", async () => {
    listMonitors.mockResolvedValue({ monitors: [MONITOR] });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
    getBusinessImpactSummary.mockResolvedValue({
      total_sessions: 10, ai_sessions: 2, ai_share_pct: 20, ai_conversions: null,
      ai_conversion_rate_pct: null, ai_revenue_cents: null, confirmed_bookings: null,
      leads: null, has_data: true, is_pro: false, ga4_connected: false,
    });
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
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
    render(<MemoryRouter><BusinessImpact /></MemoryRouter>);
    await waitFor(() => screen.getByText("Could not reach the server."));
  });
});
