/* AI Traffic dashboard — the four-section layout (Traffic overview, Conversion
   performance, Top landing pages, Traffic source comparison) built on
   /integrations/google/intelligence and /events. api.js mocked at the boundary. */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

vi.mock("../api.js", () => ({
  getGa4Status: vi.fn(), getGa4Traffic: vi.fn(), getGa4Overview: vi.fn(), getGa4Trends: vi.fn(),
  getGa4Channels: vi.fn(), getGa4LandingPages: vi.fn(), getGa4Intelligence: vi.fn(), getGa4Events: vi.fn(),
  saveGa4EventMappings: vi.fn(), connectGoogleAnalytics: vi.fn(), listGa4Properties: vi.fn(),
  selectGa4Property: vi.fn(), triggerGa4Sync: vi.fn(), disconnectGoogleAnalytics: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));
let canManage = true;
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: () => canManage }) }));
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade: vi.fn() }) }));

import {
  getGa4Status, getGa4Traffic, getGa4Overview, getGa4Trends, getGa4Channels, getGa4LandingPages,
  getGa4Intelligence, getGa4Events, saveGa4EventMappings, ScanError,
} from "../api.js";
import { intelFixture, eventsFixture } from "./__fixtures__/ga4Intelligence.js";
import AiTraffic from "./AiTraffic.jsx";

const M = { total_users: 10, new_users: 4, sessions: 200, engaged_sessions: 100, views: 400,
            engagement_duration_sec: 0, key_events: 9, returning_users: 6, engagement_rate: 0.5,
            avg_engagement_time_sec: 30, conversion_rate: 0.045 };

function renderAt() {
  return render(
    <MemoryRouter initialEntries={["/app/analytics/ai-traffic"]}>
      <Routes><Route path="/app/analytics/ai-traffic" element={<AiTraffic />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  canManage = true;
  getGa4Status.mockResolvedValue({ status: "active", ga4_property_name: "Acme", ga4_property_id: "1",
                                   last_synced_at: "2026-11-02T08:00:00" });
  getGa4Traffic.mockResolvedValue({ synced: true, by_source: {}, by_source_detail: {} });
  getGa4Overview.mockResolvedValue({ current: M, previous: M, key_events_configured: true,
    date_range: { start: "2026-10-06", end: "2026-11-02" }, previous_date_range: { start: "2026-09-08", end: "2026-10-05" } });
  getGa4Trends.mockResolvedValue({ date_range: { start: "2026-10-06", end: "2026-11-02" }, daily: [], synced: true });
  getGa4Channels.mockResolvedValue({ channels: [], total: 0, page: 1, page_size: 25 });
  getGa4LandingPages.mockResolvedValue({ pages: [], total: 0, page: 1, page_size: 25, synced: true });
  getGa4Intelligence.mockResolvedValue(intelFixture());
  getGa4Events.mockResolvedValue(eventsFixture());
});

describe("Layout", () => {
  it("shows exactly the four main sections, in order", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/traffic source comparison/i));
    const sections = [...document.querySelectorAll(".sp-ga4-section")];
    expect(sections.map((s) => s.getAttribute("aria-label") || s.querySelector("h2")?.textContent)).toEqual([
      "Traffic overview", "Conversion performance", "Top landing pages", "Traffic source comparison",
    ]);
    expect(screen.queryByText(/^insights$/i)).toBeNull();
  });

  it("the overview strip reports AI and organic sessions from synced data", async () => {
    renderAt();
    await waitFor(() => screen.getByText("AI assistant sessions"));
    const strip = document.querySelector(".sp-ga4-classstrip");
    within(strip).getByText("50");
    within(strip).getByText("25%");
    within(strip).getByText("100");
  });
});

describe("Conversion performance", () => {
  it("compares AI and organic conversions per 100 sessions, says what is counted, and how attribution works", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/counting your ga4 key events/i));
    const card = screen.getByText(/counting your ga4 key events/i).closest(".sp-ga4-card");
    const ai = within(card).getByText("All AI assistants").closest(".sp-ga4-conv-col");
    within(ai).getByText("8");            // 4 conversions / 50 sessions x 100
    within(ai).getByText("$300");
    within(card).getByText(/session-level/i);
  });

  it("when nothing is measured it says so — never a 0", async () => {
    getGa4Intelligence.mockResolvedValue(intelFixture({ measured: false }));
    renderAt();
    await waitFor(() => screen.getByText(/conversions aren't measured yet/i));
    const strip = document.querySelector(".sp-ga4-classstrip");
    within(strip).getByText("Not measured");
    const table = screen.getByText(/traffic source comparison/i).closest(".sp-ga4-card");
    expect(within(table).getAllByText("Not measured").length).toBeGreaterThan(0);
  });

  it("without a known currency no revenue figure is shown", async () => {
    const d = intelFixture();
    d.conversions.revenue_status = "no_currency";
    getGa4Intelligence.mockResolvedValue(d);
    renderAt();
    await waitFor(() => screen.getByText(/currency couldn't be read/i));
    expect(screen.queryByText("$300")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Revenue" })).toBeNull();
  });

  it("a manager maps events to conversions, saves, and the dashboard reloads", async () => {
    saveGa4EventMappings.mockResolvedValue({ mappings: [] });
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: /configure conversions/i }));
    const select = await screen.findByLabelText("How generate_lead counts");
    fireEvent.change(select, { target: { value: "conversion:lead" } });
    screen.getByText(/booking_completed/);   // standard event status is listed
    screen.getByText(/not seen — needs website-side tracking/i);
    fireEvent.click(screen.getByText(/save conversion mapping/i));
    await waitFor(() => expect(saveGa4EventMappings).toHaveBeenCalledWith([
      { event_name: "generate_lead", role: "conversion", conversion_type: "lead", counts_revenue: false },
    ]));
    await waitFor(() => expect(getGa4Intelligence).toHaveBeenCalledTimes(2));
  });

  it("a suggestion fills the mapping only when the user accepts it", async () => {
    saveGa4EventMappings.mockResolvedValue({ mappings: [] });
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: /configure conversions/i }));
    expect((await screen.findByLabelText("How purchase counts")).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /suggested: purchase/i }));
    expect(screen.getByLabelText("How purchase counts").value).toBe("conversion:purchase");
  });

  it("a viewer sees event availability read-only", async () => {
    canManage = false;
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: /event availability/i }));
    await screen.findByText(/standard events on this property/i);
    expect(screen.queryByText(/save conversion mapping/i)).toBeNull();
    expect(screen.queryByLabelText("How purchase counts")).toBeNull();
  });
});

describe("Landing pages and data reliability", () => {
  it("top landing pages show AI and organic sessions per page", async () => {
    getGa4LandingPages.mockResolvedValue({ total: 1, page: 1, page_size: 25, synced: true, pages: [
      { landing_page: "/pricing", sessions: 165, ai_sessions: 40, organic_sessions: 100, engaged_sessions: 80,
        engagement_rate: 0.48, key_events: 3, sessions_change: null, scan: null }] });
    renderAt();
    await waitFor(() => screen.getByText("/pricing"));
    const row = screen.getByText("/pricing").closest("tr");
    within(row).getByText("40");
    within(row).getByText("100");
  });

  it("GA4 thresholding, staleness and partial coverage are stated, not hidden", async () => {
    const d = intelFixture();
    d.freshness = { ...d.freshness, stale: true, partial_range: true, covered_from: "2026-10-20",
                    data_quality: { traffic: { thresholded: true, sampled: false, other_row: true, truncated: false } } };
    getGa4Intelligence.mockResolvedValue(d);
    renderAt();
    await waitFor(() => screen.getByText(/more than 2 days old/i));
    screen.getByText(/synced history starts 2026-10-20/i);
    screen.getByText(/thresholding/i);
    screen.getByText(/"\(other\)" row/i);
  });

  it("if the intelligence request fails, overview and landing pages still render", async () => {
    getGa4Intelligence.mockRejectedValue(new ScanError("Could not load conversion and source comparison data.", 500));
    renderAt();
    await waitFor(() => expect(screen.getAllByText(/could not load conversion and source comparison data/i).length).toBe(2));
    screen.getByText(/executive overview/i);
    screen.getByText(/top landing pages/i);
  });
});
