/* Page Opportunities (Site Intelligence's business-value view) — api.js mocked at the
   boundary. Covers loading / empty / error / upgrade states, scoring progress and
   polling, "Not measured" never shown as 0, server-side filters and paging, the URL
   detail drawer, the Search Console connection panel, and the tab inside Site
   Intelligence. */
import React from "react";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../api.js", () => ({
  getPageOpportunities: vi.fn(), listPageOpportunities: vi.fn(), getPageOpportunity: vi.fn(),
  refreshPageOpportunities: vi.fn(), cancelPageOpportunitiesRun: vi.fn(),
  getSearchConsoleStatus: vi.fn(), connectSearchConsole: vi.fn(), listSearchConsoleSites: vi.fn(),
  selectSearchConsoleSite: vi.fn(),
  listMonitors: vi.fn(), getSiteIntelligence: vi.fn(), createCrawlJob: vi.fn(), cancelCrawlJob: vi.fn(),
  listCrawlUrls: vi.fn(), getCrawlUrl: vi.fn(), compareSiteIntelligence: vi.fn(), listCrawlTemplates: vi.fn(),
  getSiteProfile: vi.fn(), listUrlFindings: vi.fn(), updateFindingStatus: vi.fn(),
  bulkUpdateFindingStatus: vi.fn(), recrawlFinding: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));
const openUpgrade = vi.fn();
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade }) }));
let perms = { "scan:run": true, "org:update": true };
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: (p) => !!perms[p] }) }));

import {
  getPageOpportunities, listPageOpportunities, getPageOpportunity, refreshPageOpportunities,
  getSearchConsoleStatus, connectSearchConsole, listSearchConsoleSites, selectSearchConsoleSite,
  listMonitors, getSiteIntelligence, ScanError,
} from "../api.js";
import PageOpportunities, { PO_POLL_MS } from "./PageOpportunities.jsx";
import SiteIntelligence from "./SiteIntelligence.jsx";

const DEFS = { score: "A high score does not guarantee revenue.", search_console: "Search Console is not AI data." };
const SOURCES = {
  window: { start: "2026-10-06", end: "2026-11-02", days: 28 },
  ga4: { available: true, property_name: "Acme", as_of: "2026-11-02T08:00:00", source_split_complete: true },
  gsc: { available: false, reason: "Search Console isn't connected." },
  first_party: { available: false }, links: { available: true },
  conversion_source: "ga4_key_events", revenue_available: true, revenue_currency: "USD",
};
const results = (over = {}) => ({
  run: { id: "r1", status: "completed" }, scored_at: "2026-11-02T09:00:00", stale: false,
  counts: { discovered_urls: 1200, scored_urls: 1100, commercial_pages: 340, high_priority: 12,
            pages_with_ai_traffic: 45, pages_with_organic_traffic: 300, pages_with_search_clicks: null,
            pages_with_conversions: 18 },
  page_types: {}, classification_status: {}, sources: SOURCES, ...over,
});
const overview = (over = {}) => ({
  website: { id: "m1" }, has_completed_crawl: true, latest_run: { id: "r1", status: "completed", active: false },
  results: results(), definitions: DEFS, ...over,
});
const ROW = {
  crawl_url_id: "u1", url: "https://acme.example/products/widget", page_title: "Widget", page_type: "product",
  classification_status: "confirmed", classification_confidence: 0.9, is_commercial: true, score: 78.4,
  measured_signals: 4, priority: "high", ai_sessions: 12, organic_sessions: 40, total_sessions: 60,
  conversions: 3, conversion_source: "ga4_key_events", revenue: 250, revenue_currency: "USD",
  gsc_clicks: null, gsc_impressions: null, gsc_position: null, inbound_links: 9, issue_count: 1,
  main_issue: { code: "missing_meta_description", title: "Missing meta description", severity: "medium", source: "crawl" },
};
const UNSURE = { ...ROW, crawl_url_id: "u2", url: "https://acme.example/x", page_title: null, page_type: "unknown",
  classification_status: "uncertain", score: 12, priority: "none", ai_sessions: 0, conversions: null, revenue: null,
  main_issue: null };
const LIST = { total: 2, page: 1, page_size: 25, scored: true, pages: [ROW, UNSURE] };
const DETAIL = {
  ...ROW,
  classification: { page_type: "product", status: "confirmed", confidence: 0.9,
                    evidence: ["URL matches a product path.", "The page's structured data declares Product."] },
  why_it_matters: ["Sessions that started on this page produced 3 GA4 key events in the last 28 days.",
                   "12 sessions arrived from AI assistants."],
  score_breakdown: [
    { key: "purpose", label: "Page purpose", kind: "inferred", available: true, points: 25, max_points: 25, note: "Product page" },
    { key: "search", label: "Google Search visibility", kind: "measured", available: false, points: null, max_points: 10,
      note: "Not measured: Search Console isn't connected for this website" },
  ],
  priority_reason: "Business Value Score 78.4 with a medium-severity issue.",
  issues: [{ code: "missing_meta_description", title: "Missing meta description", severity: "medium", observed: "no meta description" }],
  data_signals: [], findings: [{ id: "f1" }],
  recommended_action: { kind: "finding", title: "Missing meta description — 3 of 4 product pages",
                        action: "Add a meta description.", evidence: "Checked on this page.", finding_id: "f1" },
  sources: SOURCES,
};

function renderPO(props = {}) {
  const onGated = vi.fn(() => true);
  const onShowSiteHealth = vi.fn();
  render(
    <MemoryRouter>
      <PageOpportunities monitorId="m1" canRun canManage onGated={onGated} onShowSiteHealth={onShowSiteHealth} {...props} />
    </MemoryRouter>,
  );
  return { onGated, onShowSiteHealth };
}

beforeEach(() => {
  vi.resetAllMocks();
  perms = { "scan:run": true, "org:update": true };
  getPageOpportunities.mockResolvedValue(overview());
  listPageOpportunities.mockResolvedValue(LIST);
  getPageOpportunity.mockResolvedValue(DETAIL);
  getSearchConsoleStatus.mockResolvedValue({ status: "not_connected" });
});
afterEach(() => { vi.useRealTimers(); });

describe("states", () => {
  it("shows a skeleton while loading, then the KPIs", async () => {
    let resolve;
    getPageOpportunities.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderPO();
    expect(document.querySelector(".sp-po").textContent).toBe("");   // skeleton only
    await act(async () => resolve(overview()));
    const kpis = document.querySelector(".sp-po-kpis");
    within(kpis).getByText("1,200");
    within(kpis).getByText("340");
    within(kpis).getByText("12");
  });

  it("a source that isn't available is 'Not measured', never 0", async () => {
    getPageOpportunities.mockResolvedValue(overview({ results: results({
      counts: { ...results().counts, pages_with_ai_traffic: null, pages_with_conversions: 0 } }) }));
    renderPO();
    const kpi = (label) => screen.getByText(label).closest(".sp-po-kpi");
    await screen.findByText("Pages with AI traffic");
    within(kpi("Pages with AI traffic")).getByText("Not measured");
    within(kpi("Pages with conversions")).getByText("0");          // measured zero
    within(kpi("Pages with organic traffic")).getByText("300");
  });

  it("without a completed analysis it points to the site analysis", async () => {
    getPageOpportunities.mockResolvedValue(overview({ has_completed_crawl: false, results: null, latest_run: null }));
    const { onShowSiteHealth } = renderPO();
    fireEvent.click(await screen.findByRole("button", { name: /go to site analysis/i }));
    expect(onShowSiteHealth).toHaveBeenCalled();
  });

  it("offers to score pages when an analysis exists but nothing was scored", async () => {
    getPageOpportunities.mockResolvedValue(overview({ results: null, latest_run: null }));
    refreshPageOpportunities.mockResolvedValue({ id: "r2", status: "pending", active: true });
    renderPO();
    fireEvent.click(await screen.findByRole("button", { name: /score pages/i }));
    await waitFor(() => expect(refreshPageOpportunities).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(getPageOpportunities).toHaveBeenCalledTimes(2));
  });

  it("a viewer can't start scoring", async () => {
    getPageOpportunities.mockResolvedValue(overview({ results: null, latest_run: null }));
    renderPO({ canRun: false });
    await screen.findByText(/ask an admin or member/i);
    expect(screen.queryByRole("button", { name: /score pages/i })).toBeNull();
  });

  it("shows scoring progress and stops polling once the run finishes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getPageOpportunities
      .mockResolvedValueOnce(overview({ results: null, latest_run: { id: "r2", status: "running", active: true,
                                                                     total_urls: 4000, processed_urls: 1000 } }))
      .mockResolvedValue(overview());
    renderPO();
    await screen.findByText("1,000 of 4,000 pages");
    await act(async () => { await vi.advanceTimersByTimeAsync(PO_POLL_MS); });
    await waitFor(() => expect(document.querySelector(".sp-po-kpis")).not.toBeNull());
    await act(async () => { await vi.advanceTimersByTimeAsync(PO_POLL_MS * 3); });
    expect(getPageOpportunities).toHaveBeenCalledTimes(2);
  });

  it("an error is shown, not a blank page", async () => {
    getPageOpportunities.mockRejectedValue(new ScanError("Could not load Page Opportunities right now.", 500));
    renderPO();
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not load page opportunities/i);
  });

  it("a free plan sees the upgrade prompt", async () => {
    getPageOpportunities.mockRejectedValue(new ScanError("Page Opportunities is a Pro feature.", 402));
    const { onGated } = renderPO();
    fireEvent.click(await screen.findByRole("button", { name: /see pro plans/i }));
    expect(onGated).toHaveBeenCalled();
    expect(listPageOpportunities).not.toHaveBeenCalled();
  });
});

describe("page table", () => {
  it("lists pages with value, traffic, Google data, conversions, revenue, main issue and priority", async () => {
    renderPO();
    const row = (await screen.findByText("Widget")).closest("tr");
    within(row).getByText("78");
    within(row).getByText("12");
    within(row).getByText("$250");
    within(row).getByText("Missing meta description");
    within(row).getByText("High");
    within(row).getByText("Not measured");            // Search Console not connected
    const unsure = screen.getByText("https://acme.example/x").closest("tr");
    within(unsure).getByText(/uncertain/);
    within(unsure).getByText("0");                     // a measured zero stays 0
    expect(within(unsure).getAllByText("Not measured").length).toBeGreaterThan(0);
  });

  it("hides the revenue column when revenue isn't available", async () => {
    getPageOpportunities.mockResolvedValue(overview({ results: results({ sources: { ...SOURCES, revenue_available: false } }) }));
    renderPO();
    await screen.findByText("Widget");
    expect(screen.queryByRole("columnheader", { name: "Revenue" })).toBeNull();
  });

  it("filters and sorts on the server and resets to page 1", async () => {
    listPageOpportunities.mockResolvedValue({ ...LIST, total: 60 });
    renderPO();
    await screen.findByText("Widget");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(listPageOpportunities).toHaveBeenLastCalledWith("m1", expect.objectContaining({ page: 2 })));
    fireEvent.change(screen.getByLabelText("Page type"), { target: { value: "booking" } });
    fireEvent.change(screen.getByLabelText("Data filter"), { target: { value: "ai" } });
    fireEvent.click(screen.getByLabelText(/commercial only/i));
    fireEvent.change(screen.getByLabelText("Sort pages"), { target: { value: "-gsc_clicks" } });
    fireEvent.change(screen.getByLabelText("Search pages"), { target: { value: "widget" } });
    fireEvent.submit(screen.getByLabelText("Search pages").closest("form"));
    await waitFor(() => expect(listPageOpportunities).toHaveBeenLastCalledWith("m1", expect.objectContaining({
      page_type: "booking", has: "ai", commercial: "true", sort: "-gsc_clicks", q: "widget", page: 1 })));
  });

  it("shows an empty state when no page matches", async () => {
    listPageOpportunities.mockResolvedValue({ total: 0, page: 1, page_size: 25, scored: true, pages: [] });
    renderPO();
    await screen.findByText(/no pages match these filters/i);
  });
});

describe("URL detail", () => {
  it("explains why the page matters, its classification, data, issues and next action", async () => {
    renderPO();
    fireEvent.click(await screen.findByText("Widget"));
    const dlg = await screen.findByRole("dialog", { name: /page opportunity/i });
    await within(dlg).findByText(/recommended next action/i);
    within(dlg).getByText("Missing meta description — 3 of 4 product pages");
    expect(within(dlg).getByRole("link", { name: /open in action center/i }).getAttribute("href")).toBe("/app/action-center");
    within(dlg).getByText(/12 sessions arrived from AI assistants/);
    within(dlg).getByText("Confirmed");
    within(dlg).getByText("URL matches a product path.");
    expect(within(dlg).getAllByText(/not measured: search console isn't connected/i).length).toBeGreaterThan(0);
    within(dlg).getByText(/doesn't guarantee revenue/i);
    fireEvent.click(within(dlg).getByText(/how the score was calculated/i));
    within(dlg).getByText("Inferred");
    within(dlg).getByText("25 / 25");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("a detail error is shown inside the drawer", async () => {
    getPageOpportunity.mockRejectedValue(new ScanError("Page not found.", 404));
    renderPO();
    fireEvent.click(await screen.findByText("Widget"));
    await within(await screen.findByRole("dialog")).findByText("Page not found.");
  });
});

describe("Search Console", () => {
  it("connect sends the browser to Google's consent page", async () => {
    connectSearchConsole.mockResolvedValue({ authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    const assign = vi.fn();
    const orig = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...orig, assign } });
    try {
      renderPO();
      fireEvent.click(await screen.findByRole("button", { name: /connect search console/i }));
      await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"));
      screen.getByText(/webmasters\.readonly/);
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: orig });
    }
  });

  it("after connecting, an admin picks a verified property", async () => {
    getSearchConsoleStatus.mockResolvedValueOnce({ status: "pending_property" })
      .mockResolvedValue({ status: "active", site_url: "sc-domain:acme.example", covered_monitor_ids: ["m1"] });
    listSearchConsoleSites.mockResolvedValue({ sites: [{ site_url: "sc-domain:acme.example" }] });
    selectSearchConsoleSite.mockResolvedValue({ status: "ok" });
    renderPO({ gscFlash: { status: "connected" } });
    screen.getByText(/search console connected/i);
    fireEvent.change(await screen.findByLabelText("Search Console property"), { target: { value: "sc-domain:acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: /use this property/i }));
    await waitFor(() => expect(selectSearchConsoleSite).toHaveBeenCalledWith("sc-domain:acme.example"));
    await screen.findByText(/connected to sc-domain:acme\.example/i);
  });

  it("says when the property doesn't cover this website, and maps OAuth errors", async () => {
    getSearchConsoleStatus.mockResolvedValue({ status: "active", site_url: "sc-domain:other.org", covered_monitor_ids: [] });
    renderPO({ gscFlash: { status: "error", reason: "scope_not_granted" } });
    await screen.findByText(/doesn't cover this website's address/i);
    screen.getByText(/access wasn't granted/i);
  });

  it("a non-admin can't connect", async () => {
    renderPO({ canManage: false });
    await screen.findByText(/ask an admin to connect search console/i);
    expect(screen.queryByRole("button", { name: /connect search console/i })).toBeNull();
  });
});

describe("inside Site Intelligence", () => {
  it("is a tab of Site Intelligence, not a separate dashboard", async () => {
    listMonitors.mockResolvedValue({ monitors: [{ id: "m1", name: "Acme" }] });
    getSiteIntelligence.mockResolvedValue({ website: { id: "m1" }, is_pro: true, can_start: true, latest_job: null,
                                            status: null, results: null });
    const onChangeView = vi.fn();
    const { rerender } = render(
      <MemoryRouter><SiteIntelligence monitorId="m1" onSelectMonitor={vi.fn()} onOpenMonitor={vi.fn()}
                                      view="health" onChangeView={onChangeView} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("tab", { name: "Page opportunities" }));
    expect(onChangeView).toHaveBeenCalledWith("opportunities");
    rerender(
      <MemoryRouter><SiteIntelligence monitorId="m1" onSelectMonitor={vi.fn()} onOpenMonitor={vi.fn()}
                                      view="opportunities" onChangeView={onChangeView} /></MemoryRouter>);
    await screen.findByText("Widget");
    expect(screen.getByRole("tab", { name: "Page opportunities" }).getAttribute("aria-selected")).toBe("true");
  });
});
