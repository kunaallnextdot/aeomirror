/* Site Intelligence page — rendering and interaction with api.js mocked at the
   boundary. Covers website selection, every analysis state the backend reports
   (including stall and warnings), polling that stops on terminal states, evidence-
   labeled findings, the server-side inventory (debounced search, filters, paging) and
   the page detail panel. */
import React from "react";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../api.js", () => ({
  listMonitors: vi.fn(), getSiteIntelligence: vi.fn(), createCrawlJob: vi.fn(), cancelCrawlJob: vi.fn(),
  listCrawlUrls: vi.fn(), getCrawlUrl: vi.fn(), compareSiteIntelligence: vi.fn(),
  listCrawlTemplates: vi.fn(), getSiteProfile: vi.fn(), listUrlFindings: vi.fn(),
  updateFindingStatus: vi.fn(), bulkUpdateFindingStatus: vi.fn(), recrawlFinding: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));
const openUpgrade = vi.fn();
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade }) }));
let canRun = true;
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: () => canRun }) }));

import {
  listMonitors, getSiteIntelligence, createCrawlJob, cancelCrawlJob, listCrawlUrls, getCrawlUrl,
  updateFindingStatus, ScanError,
} from "../api.js";
import SiteIntelligence, { POLL_MS } from "./SiteIntelligence.jsx";

const SITES = [{ id: "m1", name: "Acme", domain: "acme.example" }, { id: "m2", name: "Beta", domain: "beta.example" }];
const COUNTS = {
  discovered: 12, in_scope: 10, skipped: 2, pending: 1, crawled: 8, failed: 1, analyzed: 8,
  with_issues: 5, issues_evaluated: 8, crawl_coverage_pct: 80.0, analysis_coverage_pct: 100.0,
  definitions: { crawl_coverage_pct: "Crawled ÷ in-scope URLs (discovered minus skipped).",
                 analysis_coverage_pct: "Crawled pages whose template has been analyzed ÷ crawled pages." },
};
const FINDING = {
  id: "f1", finding_title: "Missing meta description — 3 of 4 product pages", priority: "medium",
  kind: "confirmed", evidence_basis: "deterministic", affected_url_count: 3, status: "open",
  why_it_matters: "Search engines pick their own snippet.", recommended_fix: "Add a meta description.",
  verification_procedure: "Re-check the page.", affected_urls: ["https://acme.example/p/1", "https://acme.example/p/2"],
  observations: [{ url: "https://acme.example/p/1", observed: "no meta description" }],
  priority_reason: "medium technical severity (x2)", confidence: "high", evidence_limitations: null,
};
const AI_FINDING = { ...FINDING, id: "f2", finding_title: "Product specs are thin", kind: "potential",
  evidence_basis: "ai_representative", affected_urls: ["https://acme.example/p/1"], observations: null };
const completed = (over = {}) => ({
  website: { id: "m1", domain: "acme.example" }, is_pro: true, can_start: true,
  latest_job: { id: "j1", phase: "completed" },
  status: { phase: "completed", outcome: "completed", label: "Completed", active: false, can_cancel: false, warnings: [], counts: COUNTS },
  results: {
    job: { id: "j1", finished_at: "2026-10-28T10:00:00" }, counts: COUNTS,
    status: { outcome: "completed", warnings: [] },
    findings: { open: 2, total: 2, confirmed: 1, potential: 1, insufficient_evidence: 0, by_priority: {} },
    top_priorities: [FINDING, AI_FINDING], locked_count: 0,
    comparison: { available: false, reason: "no_baseline", message: "There is no earlier completed analysis of this website to compare with." },
  },
  ...over,
});
const crawling = (over = {}) => ({
  website: { id: "m1" }, is_pro: true, can_start: false, latest_job: { id: "j2", phase: "crawling" },
  status: { phase: "crawling", label: "Crawling pages", description: "Fetching each page…", active: true, can_cancel: true,
            progress: { done: 3, total: 4, unit: "pages fetched", note: "The total grows as links on crawled pages are discovered." },
            last_progress_at: "2026-10-29T10:00:00", stalled: false, warnings: [], counts: COUNTS },
  results: null, ...over,
});
const PAGE_ROWS = { total: 2, page: 1, page_size: 50, urls: [
  { id: "u1", url: "https://acme.example/p/1", page_title: "Widget", page_type: "product", status: "crawled",
    analyzed: true, http_status: 200, issue_count: 2, priority: "high", crawled_at: "2026-10-28T09:00:00" },
  { id: "u2", url: "https://acme.example/old", page_title: null, page_type: null, status: "failed",
    analyzed: false, http_status: null, issue_count: null, priority: null, crawled_at: null },
] };

function renderPage(props = {}) {
  const onSelectMonitor = vi.fn();
  render(
    <MemoryRouter>
      <SiteIntelligence monitorId="m1" onSelectMonitor={onSelectMonitor} onOpenMonitor={vi.fn()} {...props} />
    </MemoryRouter>,
  );
  return { onSelectMonitor };
}

beforeEach(() => {
  vi.resetAllMocks();
  canRun = true;
  listMonitors.mockResolvedValue({ monitors: SITES });
  listCrawlUrls.mockResolvedValue(PAGE_ROWS);
  getSiteIntelligence.mockResolvedValue(completed());
});
afterEach(() => { vi.useRealTimers(); });

describe("Website context", () => {
  it("with no website in the URL, opens the organization's first website (replacing history)", async () => {
    const { onSelectMonitor } = renderPage({ monitorId: null });
    await waitFor(() => expect(onSelectMonitor).toHaveBeenCalledWith("m1", { replace: true }));
    expect(getSiteIntelligence).not.toHaveBeenCalled();
  });

  it("a website id that isn't in this organization is refused and nothing of it is shown", async () => {
    // The backend 404s it regardless (org-scoped); the page also refuses to render it.
    renderPage({ monitorId: "someone-else" });
    await waitFor(() => screen.getByText(/isn't in your organization/i));
    expect(screen.queryByText(/what to fix first/i)).toBeNull();
    expect(screen.queryByText(FINDING.finding_title)).toBeNull();
  });

  it("switching website in the selector navigates to that website", async () => {
    const { onSelectMonitor } = renderPage();
    await waitFor(() => screen.getByLabelText("Website"));
    fireEvent.change(screen.getByLabelText("Website"), { target: { value: "m2" } });
    expect(onSelectMonitor).toHaveBeenCalledWith("m2");
  });

  it("an organization with no websites is told how to begin", async () => {
    listMonitors.mockResolvedValue({ monitors: [] });
    renderPage({ monitorId: null });
    await waitFor(() => screen.getByText(/add a website first/i));
  });
});

describe("Analysis state", () => {
  it("no analysis yet: explains it and offers to start one", async () => {
    getSiteIntelligence.mockResolvedValue({ website: { id: "m1", domain: "acme.example" }, can_start: true,
      latest_job: null, status: null, results: null, is_pro: true });
    createCrawlJob.mockResolvedValue({ id: "j9", phase: "pending" });
    renderPage();
    await waitFor(() => screen.getByText(/analyze acme.example/i));
    fireEvent.click(screen.getAllByText(/start site analysis/i)[0]);
    await waitFor(() => expect(createCrawlJob).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(getSiteIntelligence).toHaveBeenCalledTimes(2));   // reloaded
  });

  it("a member without run permission sees no start or cancel controls", async () => {
    canRun = false;
    getSiteIntelligence.mockResolvedValue(crawling());
    renderPage();
    await waitFor(() => screen.getByText("Crawling pages"));
    expect(screen.queryByText(/cancel analysis/i)).toBeNull();
    expect(screen.queryByText(/run a new analysis/i)).toBeNull();
  });

  it("shows the stage and only the progress the backend reports", async () => {
    getSiteIntelligence.mockResolvedValue(crawling());
    renderPage();
    await waitFor(() => screen.getByText("Crawling pages"));
    screen.getByText(/3 of 4 pages fetched/);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("3");
    expect(bar.getAttribute("aria-valuemax")).toBe("4");
  });

  it("discovery with no known total shows a count, never a progress bar", async () => {
    getSiteIntelligence.mockResolvedValue(crawling({ status: { ...crawling().status, phase: "discovering",
      label: "Discovering URLs", progress: { done: 40, total: null, unit: "URLs found so far" } } }));
    renderPage();
    await waitFor(() => screen.getByText(/40 URLs found so far/));
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("a stalled analysis says so and can be cancelled", async () => {
    getSiteIntelligence.mockResolvedValue(crawling({ status: { ...crawling().status, stalled: true,
      stalled_note: "No progress for over 15 minutes." } }));
    cancelCrawlJob.mockResolvedValue({ id: "j2", phase: "cancelled" });
    renderPage();
    await waitFor(() => screen.getByText(/no progress for over 15 minutes/i));
    fireEvent.click(screen.getByText(/cancel analysis/i));
    await waitFor(() => expect(cancelCrawlJob).toHaveBeenCalledWith("m1", "j2"));
  });

  it("polls while running and stops as soon as the analysis is terminal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getSiteIntelligence.mockResolvedValueOnce(crawling()).mockResolvedValueOnce(completed());
    renderPage();
    await waitFor(() => screen.getByText("Crawling pages"));
    expect(getSiteIntelligence).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await waitFor(() => expect(getSiteIntelligence).toHaveBeenCalledTimes(2));
    await waitFor(() => screen.getByText(/what to fix first/i));
    await act(async () => { vi.advanceTimersByTime(POLL_MS * 3); });
    expect(getSiteIntelligence).toHaveBeenCalledTimes(2);       // terminal: no more polling
  });

  it("a load error is shown, not a blank page", async () => {
    getSiteIntelligence.mockRejectedValue(new ScanError("Could not load Site Intelligence for this website.", 500));
    renderPage();
    await waitFor(() => screen.getByRole("alert"));
    screen.getByText(/could not load site intelligence/i);
  });

  it("completed with warnings lists the warnings", async () => {
    const d = completed();
    d.results.status = { outcome: "completed_with_warnings", warnings: ["1 page(s) could not be fetched."] };
    getSiteIntelligence.mockResolvedValue(d);
    renderPage();
    await waitFor(() => screen.getByText(/completed with warnings/i));
    screen.getByText(/1 page\(s\) could not be fetched/);
  });
});

describe("Results", () => {
  it("summary metrics state their definitions; coverage is never unlabeled", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/crawl coverage/i));
    screen.getByText("80%", { exact: false });
    screen.getByText(/crawled ÷ in-scope urls/i);
    screen.getByText(/crawled pages whose template has been analyzed/i);
  });

  it("findings are labeled by kind and basis, and expand to affected pages, evidence, fix and verification", async () => {
    renderPage();
    await waitFor(() => screen.getByText(FINDING.finding_title));
    const card = screen.getByText(FINDING.finding_title).closest(".sp-si-finding");
    within(card).getByText("Confirmed issue");
    within(card).getByText("Checked on each page");
    fireEvent.click(within(card).getByText(FINDING.finding_title));
    within(card).getByText("https://acme.example/p/2");
    within(card).getByText("no meta description");
    within(card).getByText(/add a meta description/i);
    within(card).getByText(/re-check the page/i);

    const ai = screen.getByText(AI_FINDING.finding_title).closest(".sp-si-finding");
    within(ai).getByText("Potential opportunity");
    within(ai).getByText("AI analysis of a representative page");
    fireEvent.click(within(ai).getByText(AI_FINDING.finding_title));
    within(ai).getByText(/were not checked individually/i);
  });

  it("free plan: a real preview plus a locked banner, and no status controls", async () => {
    const d = completed({ is_pro: false });
    d.results.locked_count = 4;
    getSiteIntelligence.mockResolvedValue(d);
    renderPage();
    await waitFor(() => screen.getByText(/4 more finding\(s\)/));
    fireEvent.click(screen.getByText(FINDING.finding_title));
    expect(screen.queryByText(/re-check now/i)).toBeNull();
  });

  it("Pro: changing a finding's status saves it and reloads", async () => {
    updateFindingStatus.mockResolvedValue({});
    renderPage();
    await waitFor(() => screen.getByText(FINDING.finding_title));
    const card = screen.getByText(FINDING.finding_title).closest(".sp-si-finding");
    fireEvent.click(within(card).getByText(FINDING.finding_title));
    fireEvent.change(within(card).getByLabelText(/status/i), { target: { value: "in_progress" } });
    await waitFor(() => expect(updateFindingStatus).toHaveBeenCalledWith("m1", "j1", "f1", "in_progress"));
    await waitFor(() => expect(getSiteIntelligence).toHaveBeenCalledTimes(2));
  });

  it("with no valid earlier analysis, says there is nothing to compare with", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/no earlier completed analysis/i));
  });
});

describe("Inventory", () => {
  it("loads one server-side page of summary rows, sorted most-severe first", async () => {
    renderPage();
    await waitFor(() => screen.getByText("Widget"));
    expect(listCrawlUrls).toHaveBeenCalledWith("m1", "j1", expect.objectContaining({ view: "all", sort: "-issues", page: 1, page_size: 50 }));
    const row = screen.getByText("https://acme.example/old").closest("tr");
    within(row).getByText("Failed");
  });

  it("search is debounced into a single request", async () => {
    renderPage();
    await waitFor(() => screen.getByText("Widget"));
    const before = listCrawlUrls.mock.calls.length;
    const input = screen.getByLabelText("Search pages");
    for (const v of ["w", "wi", "wid"]) fireEvent.change(input, { target: { value: v } });
    await waitFor(() => expect(listCrawlUrls).toHaveBeenLastCalledWith("m1", "j1", expect.objectContaining({ q: "wid" })));
    expect(listCrawlUrls.mock.calls.length).toBe(before + 1);
  });

  it("changing a filter returns to page 1 with one request", async () => {
    listCrawlUrls.mockResolvedValue({ ...PAGE_ROWS, total: 120 });
    renderPage();
    await waitFor(() => screen.getByText("Page 1 of 3"));
    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => expect(listCrawlUrls).toHaveBeenLastCalledWith("m1", "j1", expect.objectContaining({ page: 2 })));
    const before = listCrawlUrls.mock.calls.length;
    fireEvent.click(screen.getByRole("tab", { name: "With issues" }));
    await waitFor(() => expect(listCrawlUrls).toHaveBeenLastCalledWith("m1", "j1", expect.objectContaining({ view: "has_issues", page: 1 })));
    expect(listCrawlUrls.mock.calls.length).toBe(before + 1);
  });

  it("opening a page shows its checked issues, evidence and traffic state; Escape closes it", async () => {
    getCrawlUrl.mockResolvedValue({
      id: "u1", url: "https://acme.example/p/1", status: "crawled", http_status: 200, page_type: "product",
      crawled_at: "2026-10-28T09:00:00", evidence_extracted_at: "2026-10-28T09:00:00", issues_evaluated: true,
      issues: [{ code: "missing_meta_description", title: "Missing meta description", kind: "confirmed", severity: "medium",
                 observed: "no meta description", why_it_matters: "Snippets.", recommended_fix: "Add one.", verification_procedure: "Re-check." }],
      findings: [{ id: "f1", finding_title: FINDING.finding_title, applies_because: "checked on this page", status: "open" }],
      template: { page_count: 4, is_representative: true },
      evidence: { title: "Widget", word_count: 300, image_count: 2, images_missing_alt: 1, schema: { types: [] } },
      traffic: { window_days: 28, first_party: { status: "not_configured", reason: "The AEOMirror tracking snippet hasn't recorded a visit on this website." },
                 ga4: { status: "available", sessions: 42 } },
    });
    renderPage();
    await waitFor(() => screen.getByText("Widget"));
    fireEvent.click(screen.getByText("Widget"));
    const dialog = await screen.findByRole("dialog", { name: "Page details" });
    await waitFor(() => within(dialog).getByText("Missing meta description"));
    within(dialog).getByText(/^checked on this page/);
    within(dialog).getByText("42 sessions");
    within(dialog).getByText(/hasn't recorded a visit/);
    within(dialog).getByText("2 (1 without alt)");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
