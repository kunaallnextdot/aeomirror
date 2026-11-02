/* AI Traffic — Website Intelligence dashboard additions: Executive Overview,
   Traffic Trends, Acquisition Channels, Landing Pages, AI Traffic Intelligence
   cards, and client-computed Insights. The connection-state machine itself
   (not_connected/revoked/pending_property/error/consent-modal flow) is covered by
   AiTraffic.test.jsx — this file only exercises the "active + synced" dashboard
   body. api.js mocked at the boundary, same convention as AiTraffic.test.jsx. */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

vi.mock("../api.js", () => ({
  getGa4Status: vi.fn(),
  getGa4Traffic: vi.fn(),
  getGa4Overview: vi.fn(),
  getGa4Trends: vi.fn(),
  getGa4Channels: vi.fn(),
  getGa4LandingPages: vi.fn(),
  getGa4Intelligence: vi.fn(),
  getGa4Events: vi.fn(),
  saveGa4EventMappings: vi.fn(),
  connectGoogleAnalytics: vi.fn(),
  listGa4Properties: vi.fn(),
  selectGa4Property: vi.fn(),
  triggerGa4Sync: vi.fn(),
  disconnectGoogleAnalytics: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: () => true }) }));
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade: vi.fn() }) }));

import {
  getGa4Status, getGa4Traffic, getGa4Overview, getGa4Trends, getGa4Channels,
  getGa4LandingPages,
} from "../api.js";
import { getGa4Intelligence, getGa4Events, saveGa4EventMappings } from "../api.js";
import { intelFixture, eventsFixture } from "./__fixtures__/ga4Intelligence.js";
import AiTraffic from "./AiTraffic.jsx";

function renderAt() {
  return render(
    <MemoryRouter initialEntries={["/app/analytics/ai-traffic"]}>
      <Routes><Route path="/app/analytics/ai-traffic" element={<AiTraffic />} /></Routes>
    </MemoryRouter>
  );
}

const M = (over = {}) => ({
  total_users: 0, new_users: 0, sessions: 0, engaged_sessions: 0, views: 0,
  engagement_duration_sec: 0, key_events: 0, returning_users: 0,
  engagement_rate: null, avg_engagement_time_sec: null, conversion_rate: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  getGa4Status.mockResolvedValue({
    status: "active", ga4_property_name: "Acme Main", ga4_property_id: "999",
    last_synced_at: "2026-10-27T10:00:00Z",
  });
  getGa4Traffic.mockResolvedValue({ synced: true, by_source: {}, by_source_detail: {} });
  getGa4Overview.mockResolvedValue({
    date_range: { start: "2026-09-29", end: "2026-10-27" },
    previous_date_range: { start: "2026-08-31", end: "2026-09-28" },
    current: M(), previous: M(), key_events_configured: null,
  });
  getGa4Trends.mockResolvedValue({ date_range: { start: "2026-09-29", end: "2026-10-27" }, daily: [] });
  getGa4Channels.mockResolvedValue({ channels: [], total: 0, page: 1, page_size: 25 });
  getGa4LandingPages.mockResolvedValue({ pages: [], total: 0, page: 1, page_size: 25 });
  getGa4Intelligence.mockResolvedValue(intelFixture());
  getGa4Events.mockResolvedValue(eventsFixture());
});

// The source/medium/campaign table is progressive disclosure inside "Traffic source
// comparison": open it the way a user would before interacting with it.
async function openSourceDetail() {
  const toggle = await screen.findByRole("button", { name: /source \/ medium detail/i });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  return await waitFor(() => {
    const el = document.querySelector(".sp-ga4-embedded");
    if (!el) throw new Error("detail not open yet");
    return el;
  });
}

describe("Executive Overview", () => {
  it("shows real KPI values from the API, distinguishing zero from unavailable", async () => {
    getGa4Overview.mockResolvedValue({
      date_range: { start: "2026-09-29", end: "2026-10-27" },
      previous_date_range: { start: "2026-08-31", end: "2026-09-28" },
      current: M({ total_users: 1200, new_users: 300, returning_users: 900, sessions: 1500,
                   engagement_rate: 0.62, key_events: 0 }),
      previous: M({ total_users: 1000 }),
      key_events_configured: true,
    });
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    screen.getByText("1,200");
    screen.getByText("300");
    screen.getByText("900");
    screen.getByText("62.0%");
    // key_events_configured is true but the count is really 0 — must show "0", not "Not configured".
    expect(screen.queryByText(/not configured in ga4/i)).toBeNull();
  });

  it("shows 'Not configured' for key events when GA4 has none configured, never a fabricated zero", async () => {
    getGa4Overview.mockResolvedValue({
      date_range: { start: "2026-09-29", end: "2026-10-27" },
      previous_date_range: { start: "2026-08-31", end: "2026-09-28" },
      current: M({ sessions: 50, key_events: 0 }), previous: M(),
      key_events_configured: false,
    });
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    screen.getByText(/not configured in ga4/i);
    screen.getByText(/no key events are configured on this ga4 property yet/i);
  });

  it("shows a previous-period delta only when the comparison toggle is on", async () => {
    getGa4Overview.mockResolvedValue({
      date_range: { start: "2026-09-29", end: "2026-10-27" },
      previous_date_range: { start: "2026-08-31", end: "2026-09-28" },
      current: M({ sessions: 200 }), previous: M({ sessions: 100 }),
      key_events_configured: null,
    });
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    screen.getByText("+100%");

    fireEvent.click(screen.getByLabelText(/compare to previous period/i));
    await waitFor(() => expect(screen.queryByText("+100%")).toBeNull());
  });

  it("never claims the dashboard is 'not active' when the connection genuinely is active", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    expect(screen.queryByText(/overview totals aren't available/i)).toBeNull();
  });
});

describe("Date range selector", () => {
  it("defaults to 28 days and re-fetches everything when a different range is picked", async () => {
    renderAt();
    await waitFor(() => expect(getGa4Overview).toHaveBeenCalled());
    const firstCallArgs = getGa4Overview.mock.calls[0][0];
    expect(firstCallArgs.startDate).toBeTruthy();

    fireEvent.click(screen.getByText("90 days"));
    await waitFor(() => expect(getGa4Overview.mock.calls.length).toBeGreaterThan(1));
    const secondCallArgs = getGa4Overview.mock.calls.at(-1)[0];
    expect(secondCallArgs.startDate).not.toBe(firstCallArgs.startDate);
  });

  it("shows From/To date inputs only when Custom is selected", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    expect(screen.queryByText("From")).toBeNull();
    fireEvent.click(screen.getByText("Custom"));
    await waitFor(() => screen.getByText("From"));
    screen.getByText("To");
  });
});

describe("Traffic Trends", () => {
  it("shows an honest empty-chart caption when there is no data, never a fabricated trend line", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/traffic trends/i));
    screen.getByText(/no traffic recorded for this period yet/i);
  });

  it("switches between Users, Sessions & engagement, and AI referral views", async () => {
    getGa4Trends.mockResolvedValue({
      date_range: { start: "2026-09-29", end: "2026-10-27" },
      daily: [{ date: "20261001", total_users: 10, new_users: 4, returning_users: 6,
                sessions: 12, engaged_sessions: 8, engagement_rate: 0.67, views: 20,
                key_events: 1,
                ai_sessions: { chatgpt: 3, perplexity: 0, gemini: 0, copilot: 0, claude: 0,
                              other_identifiable: 0, unattributable: 9 } }],
    });
    renderAt();
    await waitFor(() => screen.getByText(/traffic trends/i));
    expect(screen.queryByText(/no traffic recorded/i)).toBeNull();
    fireEvent.click(screen.getByText("AI referral sessions"));
    fireEvent.click(screen.getByText("Sessions & engagement"));
    // No crash switching views; chart container is present.
    screen.getByText(/traffic trends/i);
  });
});

describe("Acquisition Channels", () => {
  const CHANNEL_ROWS = {
    channels: [
      { channel: "Organic Search", source: "google", medium: "organic", ai_source_label: "unattributable",
        sessions: 300, new_users: 100, engaged_sessions: 150, views: 400, key_events: 2,
        engagement_rate: 0.5, conversion_rate: 0.0067 },
      { channel: "Referral", source: "chatgpt.com", medium: "referral", ai_source_label: "chatgpt",
        sessions: 50, new_users: 20, engaged_sessions: 40, views: 90, key_events: 5,
        engagement_rate: 0.8, conversion_rate: 0.1 },
    ],
    total: 2, page: 1, page_size: 25,
  };

  it("renders real channel rows with engagement and conversion rates, and never a Users column", async () => {
    getGa4Channels.mockResolvedValue(CHANNEL_ROWS);
    renderAt();
    const section = await openSourceDetail();
    within(section).getByText("Organic Search");
    within(section).getByText("50.0%");
    within(section).getByText(/users aren't shown per channel/i);
    expect(within(section).queryByText(/^users$/i)).toBeNull();
  });

  it("typing in the search box re-fetches channels with the q param", async () => {
    getGa4Channels.mockResolvedValue(CHANNEL_ROWS);
    renderAt();
    await openSourceDetail();
    fireEvent.change(screen.getByPlaceholderText(/search channel, source, or medium/i), {
      target: { value: "chatgpt" },
    });
    await waitFor(() => {
      const last = getGa4Channels.mock.calls.at(-1)[0];
      expect(last.q).toBe("chatgpt");
    });
  });

  it("clicking a sortable column header toggles the sort param", async () => {
    getGa4Channels.mockResolvedValue(CHANNEL_ROWS);
    renderAt();
    const section = await openSourceDetail();
    fireEvent.click(within(section).getByText("New users"));
    await waitFor(() => {
      const last = getGa4Channels.mock.calls.at(-1)[0];
      expect(last.sort).toBe("-new_users");
    });
    fireEvent.click(within(section).getByText("New users"));
    await waitFor(() => {
      const last = getGa4Channels.mock.calls.at(-1)[0];
      expect(last.sort).toBe("new_users");
    });
  });
});

describe("Landing Pages", () => {
  it("shows a concise empty message when there's no landing-page data yet", async () => {
    renderAt();
    await waitFor(() => screen.getByText(/landing pages/i));
    screen.getByText(/not enough landing-page data recorded/i);
  });

  it("flags a high-traffic, low-engagement page as evidence-backed, and only that page", async () => {
    getGa4LandingPages.mockResolvedValue({
      pages: [
        { landing_page: "/pricing", sessions: 400, engaged_sessions: 60, views: 500,
          key_events: 0, engagement_rate: 0.15, sessions_change: 10, scan: null },
        { landing_page: "/blog/post", sessions: 50, engaged_sessions: 40, views: 60,
          key_events: 1, engagement_rate: 0.8, sessions_change: -2, scan: null },
      ],
      total: 2, page: 1, page_size: 25,
    });
    renderAt();
    await waitFor(() => screen.getByText(/landing pages/i));
    screen.getByText("/pricing");
    const expandButtons = screen.getAllByRole("button", { name: "" });
    fireEvent.click(expandButtons[expandButtons.length - 2]);
    await waitFor(() => screen.getByText(/received 400 sessions this period but/i));
  });

  it("links out to the matching AEOMirror scan when one is found, and says so honestly when none is", async () => {
    getGa4LandingPages.mockResolvedValue({
      pages: [
        { landing_page: "/about", sessions: 30, engaged_sessions: 20, views: 40,
          key_events: 0, engagement_rate: 0.67, sessions_change: 0,
          scan: { scan_id: "scan-123", score: 78 } },
      ],
      total: 1, page: 1, page_size: 25,
    });
    renderAt();
    await waitFor(() => screen.getByText(/landing pages/i));
    fireEvent.click(screen.getByRole("button", { name: "" }));
    await waitFor(() => screen.getByRole("link", { name: /view this page's aeo audit/i }));
    const link = screen.getByRole("link", { name: /view this page's aeo audit/i });
    expect(link.getAttribute("href")).toBe("/app/scans/scan-123");
    screen.getByText(/score 78/i);
  });
});

describe("Partial failure (the production /overview incident)", () => {
  it("an /overview failure is shown on the Overview card only — trends, conversions, landing pages and source comparison still render", async () => {
    const { ScanError } = await import("../api.js");
    getGa4Overview.mockRejectedValue(new ScanError("Could not load your Google Analytics overview right now.", 502));
    getGa4Channels.mockResolvedValue({
      channels: [{ channel: "Organic Search", source: "google", medium: "organic", ai_source_label: "unattributable",
                   sessions: 300, new_users: 100, engaged_sessions: 150, views: 400, key_events: 0,
                   engagement_rate: 0.5, conversion_rate: 0 }],
      total: 1, page: 1, page_size: 25,
    });
    renderAt();
    await waitFor(() => screen.getByText(/could not load your google analytics overview right now/i));
    const overview = screen.getByText(/executive overview/i).closest(".sp-ga4-card");
    within(overview).getByText(/could not load your google analytics overview right now/i);
    screen.getByText(/traffic trends/i);
    screen.getByText(/top landing pages/i);
    // Conversions and the source comparison come from synced data, not the live
    // /overview call, so they still render.
    screen.getByText(/conversion performance/i);
    screen.getByText(/traffic source comparison/i);
    const detail = await openSourceDetail();
    within(detail).getByText("Organic Search");
  });

  it("a failure in one table doesn't hide the overview", async () => {
    const { ScanError } = await import("../api.js");
    getGa4LandingPages.mockRejectedValue(new ScanError("Could not load landing pages.", 500));
    renderAt();
    await waitFor(() => screen.getByText(/executive overview/i));
    const pages = screen.getByText(/^top landing pages$/i).closest(".sp-ga4-card");
    within(pages).getByText(/could not load landing pages/i);
    expect(screen.queryByText(/could not load your website intelligence dashboard/i)).toBeNull();
  });
});

describe("Switching GA4 property while already active", () => {
  it("lets a manager open the property picker and switch without disconnecting first", async () => {
    const { listGa4Properties, selectGa4Property } = await import("../api.js");
    listGa4Properties.mockResolvedValue({
      properties: [
        { property_id: "999", property_name: "Acme Main", account_name: "Acme" },
        { property_id: "111", property_name: "Acme EU", account_name: "Acme" },
      ],
    });
    selectGa4Property.mockResolvedValue({ status: "ok" });
    renderAt();
    await waitFor(() => screen.getByText("Acme Main"));
    fireEvent.click(screen.getByText(/change property/i));
    await waitFor(() => screen.getByText("Acme EU"));
    screen.getByText(/\(current\)/i);   // marks the currently-active property
    fireEvent.click(screen.getByText("Acme EU"));
    await waitFor(() => expect(selectGa4Property).toHaveBeenCalledWith("111"));
  });
});

describe("Request efficiency", () => {
  it("searching a table refetches only that table, once typing pauses — never the live GA4 overview", async () => {
    renderAt();
    await openSourceDetail();
    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(1));
    const counts = () => [getGa4Traffic, getGa4Overview, getGa4Trends, getGa4LandingPages]
      .map((f) => f.mock.calls.length);
    const before = counts();

    const input = screen.getByPlaceholderText(/search channel, source, or medium/i);
    for (const v of ["c", "ch", "cha", "chat"]) fireEvent.change(input, { target: { value: v } });

    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(2));   // debounced: one request
    expect(getGa4Channels).toHaveBeenLastCalledWith(expect.objectContaining({ q: "chat" }));
    expect(counts()).toEqual(before);                                       // nothing else refetched
  });

  it("changing the date range reloads every section", async () => {
    renderAt();
    await waitFor(() => expect(getGa4Overview).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /^7 days$|last 7 days/i }));
    await waitFor(() => expect(getGa4Overview).toHaveBeenCalledTimes(2));
    expect(getGa4Channels).toHaveBeenCalledTimes(2);
    expect(getGa4LandingPages).toHaveBeenCalledTimes(2);
  });
});

describe("Never shows stale data", () => {
  const chRow = (channel) => ({ channel, source: "s", medium: "m", ai_source_label: "other_identifiable",
    sessions: 5, new_users: 1, engaged_sessions: 1, views: 1, key_events: 0, engagement_rate: 0.2, conversion_rate: 0 });
  const chResp = (name) => ({ channels: [chRow(name)], total: 1, page: 1, page_size: 25 });

  it("a slow search response for the previous date range never overwrites the new range's table", async () => {
    let resolveStale;
    renderAt();
    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(1));
    await openSourceDetail();
    getGa4Channels.mockImplementationOnce(() => new Promise((r) => { resolveStale = r; }));   // the search request
    fireEvent.change(screen.getByPlaceholderText(/search channel, source, or medium/i), { target: { value: "x" } });
    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(2));

    getGa4Channels.mockResolvedValue(chResp("Fresh Channel"));                              // the new range's load
    fireEvent.click(screen.getByRole("button", { name: /^7 days$|last 7 days/i }));
    await waitFor(() => screen.getByText("Fresh Channel"));
    resolveStale(chResp("Stale Channel"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale Channel")).toBeNull();
    screen.getByText("Fresh Channel");
  });

  it("an earlier search that answers last never overwrites the later search", async () => {
    renderAt();
    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(1));
    await openSourceDetail();
    let resolveFirst;
    getGa4Channels.mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }));
    const input = screen.getByPlaceholderText(/search channel, source, or medium/i);
    fireEvent.change(input, { target: { value: "first" } });
    await waitFor(() => expect(getGa4Channels).toHaveBeenCalledTimes(2));
    getGa4Channels.mockResolvedValue(chResp("Second Channel"));
    fireEvent.change(input, { target: { value: "second" } });
    await waitFor(() => screen.getByText("Second Channel"));
    resolveFirst(chResp("First Channel"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("First Channel")).toBeNull();
    screen.getByText("Second Channel");
  });

  it("switching GA4 property reloads the dashboard instead of keeping the old property's numbers", async () => {
    const { listGa4Properties, selectGa4Property } = await import("../api.js");
    listGa4Properties.mockResolvedValue({ properties: [
      { property_id: "999", property_name: "Acme Main", account_name: "Acme" },
      { property_id: "111", property_name: "Acme EU", account_name: "Acme" },
    ] });
    selectGa4Property.mockResolvedValue({ status: "ok" });
    renderAt();
    await waitFor(() => expect(getGa4Traffic).toHaveBeenCalledTimes(1));
    getGa4Status.mockResolvedValue({ status: "active", ga4_property_name: "Acme EU", ga4_property_id: "111",
                                     last_synced_at: null });
    getGa4Traffic.mockResolvedValue({ synced: false, by_source: {}, by_source_detail: {} });
    fireEvent.click(screen.getByText(/change property/i));
    await waitFor(() => screen.getByText("Acme EU"));
    fireEvent.click(screen.getByText("Acme EU"));
    await waitFor(() => expect(getGa4Traffic).toHaveBeenCalledTimes(2));
  });
});

describe("Reporting period", () => {
  it("'28 days' is exactly 28 days including today, never 29", async () => {
    renderAt();
    await waitFor(() => expect(getGa4Intelligence).toHaveBeenCalled());
    const { startDate, endDate } = getGa4Intelligence.mock.calls[0][0];
    const days = Math.round((Date.parse(endDate) - Date.parse(startDate)) / 86400000) + 1;
    expect(days).toBe(28);
  });
});
