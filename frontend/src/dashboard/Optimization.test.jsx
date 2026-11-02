/* Optimization tab (URL-wise optimization) — api.js mocked at the boundary. Loading,
   error, Pro-gated and "not scored yet" states; the six overview counts; next best
   actions with their reasons and held-back items; the URL table and its filter; the
   re-check queue and its cancellation; before/after impact labelled as association. */
import React from "react";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  getOptimization: vi.fn(), listOptimizationUrls: vi.fn(), cancelOptimizationRechecks: vi.fn(),
  getPageOpportunity: vi.fn(), getPageIntelligence: vi.fn(), getUrlOptimization: vi.fn(),
  analyzePageNow: vi.fn(), generatePageSuggestions: vi.fn(), changeUrlAction: vi.fn(),
  getPageOpportunities: vi.fn(), listPageOpportunities: vi.fn(), refreshPageOpportunities: vi.fn(),
  cancelPageOpportunitiesRun: vi.fn(), getSearchConsoleStatus: vi.fn(), connectSearchConsole: vi.fn(),
  listSearchConsoleSites: vi.fn(), selectSearchConsoleSite: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));

import { getOptimization, listOptimizationUrls, cancelOptimizationRechecks, getPageOpportunity,
  getPageIntelligence, getUrlOptimization, ScanError } from "../api.js";
import Optimization from "./Optimization.jsx";

const OV = (over = {}) => ({
  available: true, run: { id: "r1" },
  counts: { important_urls: 12, open_issues: 31, in_progress: 2, awaiting_verification: 3, verified: 5, reopened: 1,
            not_fixed: 1, partially_fixed: 0, ignored: 1 },
  rechecks: { scheduled: 3, due_now: 1, requested: 1, next_at: "2026-11-02T10:00:00", used_today: 4, daily_limit: 200 },
  next_actions: [{ crawl_url_id: "u1", url: "https://acme.example/products/widget", page_title: "Widget", code: "noindex",
                   issue: "Page asks not to be indexed (noindex)", recommended_change: "Remove the noindex directive.",
                   priority: "high", confidence: "medium", status: "new", score: 82,
                   reasons: ["Business Value Score 82 (high value page).", "Fix this first: other changes to this page depend on it."] }],
  held_back: [{ crawl_url_id: "u2", url: "https://acme.example/b", code: "title_too_short", issue: "Page title is very short",
                reason: "Found not fixed 2 times with no change to the page since. Review the approach before trying again." }],
  impact: [{ action_id: "a1", url: "https://acme.example/c", code: "missing_h1", issue: "No H1 heading", verified_at: "2026-09-10T00:00:00",
             comparison: { excluded_period: ["2026-09-01", "2026-09-10"], window_days: 28,
                           disclaimer: "These are changes observed between the two periods, not proof that the fix caused them.",
                           sources: [] } }],
  ...over,
});
const LIST = { total: 1, page: 1, page_size: 25, urls: [{
  crawl_url_id: "u1", url: "https://acme.example/products/widget", page_title: "Widget", page_type: "product", score: 82,
  priority: "high", main_issue: { code: "noindex", issue: "Page asks not to be indexed (noindex)", status: "in_progress" },
  open_issues: 3, verification: { result: "not_fixed", at: "2026-11-01T00:00:00", method: "recrawl" },
  last_checked_at: "2026-11-01T00:00:00", next_check_at: null }] };

function renderOpt(props = {}) {
  const onGated = vi.fn();
  const onShowOpportunities = vi.fn();
  render(<MemoryRouter><Optimization monitorId="m1" canRun onGated={onGated} onShowOpportunities={onShowOpportunities} {...props} /></MemoryRouter>);
  return { onGated, onShowOpportunities };
}

beforeEach(() => {
  vi.resetAllMocks();
  getOptimization.mockResolvedValue(OV());
  listOptimizationUrls.mockResolvedValue(LIST);
});

describe("states", () => {
  it("loads, then shows the six overview counts", async () => {
    let resolve;
    getOptimization.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderOpt();
    expect(screen.queryByLabelText("Optimization overview")).toBeNull();
    await act(async () => resolve(OV()));
    const kpis = screen.getByLabelText("Optimization overview");
    for (const [label, v] of [["Important URLs", "12"], ["Open issues", "31"], ["In progress", "2"],
      ["Awaiting verification", "3"], ["Verified fixes", "5"], ["Reopened", "1"]]) {
      expect(within(kpis).getByText(label).nextSibling.textContent).toBe(v);
    }
  });

  it("an error is shown, not a blank page", async () => {
    getOptimization.mockRejectedValue(new ScanError("Server error.", 500));
    renderOpt();
    expect((await screen.findByRole("alert")).textContent).toMatch(/server error/i);
  });

  it("without scored pages it points to Page opportunities", async () => {
    getOptimization.mockResolvedValue({ available: false, reason: "Pages haven't been scored yet." });
    const { onShowOpportunities } = renderOpt();
    await screen.findByText(/haven't been scored yet/i);
    fireEvent.click(screen.getByRole("button", { name: /go to page opportunities/i }));
    expect(onShowOpportunities).toHaveBeenCalled();
  });

  it("free plans are gated", async () => {
    getOptimization.mockRejectedValue(new ScanError("Pro feature", 402));
    const { onGated } = renderOpt();
    await screen.findByText(/is a pro feature/i);
    expect(onGated).toHaveBeenCalled();
  });

  it("an empty URL list says so", async () => {
    listOptimizationUrls.mockResolvedValue({ total: 0, page: 1, page_size: 25, urls: [] });
    renderOpt();
    await screen.findByText("No URLs match.");
  });
});

describe("content", () => {
  it("next best actions explain why, and held-back actions say why they aren't repeated", async () => {
    renderOpt();
    const card = (await screen.findByText("Next best actions")).closest(".sp-si-card");
    within(card).getByText("Page asks not to be indexed (noindex)");
    within(card).getByText(/depend on it/);
    fireEvent.click(within(card).getByText(/held back \(1\)/i));
    within(card).getByText(/review the approach/i);
  });

  it("the URL table shows value, main issue, current action, verification and checks; the filter refetches", async () => {
    renderOpt();
    const row = (await screen.findAllByRole("button", { name: "Widget" })).map((x) => x.closest("tr")).find(Boolean);
    within(row).getByText("82");
    within(row).getByText("In progress");
    within(row).getByText("Not fixed");
    within(row).getByText(/\+2 more/);
    within(row).getByText("Next site crawl");
    expect(listOptimizationUrls).toHaveBeenLastCalledWith("m1", expect.objectContaining({ status: "open", page: 1 }));
    fireEvent.change(screen.getByLabelText("Status filter"), { target: { value: "verified" } });
    await waitFor(() => expect(listOptimizationUrls).toHaveBeenLastCalledWith("m1", expect.objectContaining({ status: "verified" })));
  });

  it("opening a URL shows the page drawer", async () => {
    getPageOpportunity.mockReturnValue(new Promise(() => {}));
    getPageIntelligence.mockReturnValue(new Promise(() => {}));
    getUrlOptimization.mockReturnValue(new Promise(() => {}));
    renderOpt();
    const rowBtn = (await screen.findAllByRole("button", { name: "Widget" }))[0];
    fireEvent.click(rowBtn);
    await screen.findByRole("dialog", { name: /page intelligence/i });
  });

  it("scheduled re-checks can be cancelled; viewers can't", async () => {
    cancelOptimizationRechecks.mockResolvedValue({ cancelled: 3 });
    renderOpt();
    await screen.findByText(/3 page re-checks scheduled/i);
    screen.getByText(/4 of 200 checks used today/);
    fireEvent.click(screen.getByRole("button", { name: /cancel scheduled re-checks/i }));
    await waitFor(() => expect(cancelOptimizationRechecks).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(getOptimization).toHaveBeenCalledTimes(2));
  });

  it("viewers don't get the cancel button", async () => {
    renderOpt({ canRun: false });
    await screen.findByText(/3 page re-checks scheduled/i);
    expect(screen.queryByRole("button", { name: /cancel scheduled re-checks/i })).toBeNull();
  });

  it("verified-fix performance is labelled as observed, not caused", async () => {
    renderOpt();
    const card = (await screen.findByText("Performance after verified fixes")).closest(".sp-si-card");
    fireEvent.click(within(card).getByText(/No H1 heading/));
    within(card).getByText(/not proof that the fix caused them/i);
    within(card).getByText(/left out of both/i);
  });
});
