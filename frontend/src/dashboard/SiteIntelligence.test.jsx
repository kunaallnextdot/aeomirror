/* Site Intelligence — pure rendering + interaction tests, api.js mocked at the
   boundary (same convention as BusinessImpact.test.jsx). No network, no real timers
   advanced (the polling interval never fires within a synchronous test). */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api.js", () => ({
  listCrawlJobs: vi.fn(), getCrawlJob: vi.fn(), createCrawlJob: vi.fn(), cancelCrawlJob: vi.fn(),
  listCrawlTemplates: vi.fn(), listUrlFindings: vi.fn(), updateFindingStatus: vi.fn(),
  bulkUpdateFindingStatus: vi.fn(), recrawlFinding: vi.fn(), getSiteProfile: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
vi.mock("./UpgradeModal.jsx", () => ({
  useUpgrade: () => ({ openUpgrade: vi.fn() }),
}));

import {
  listCrawlJobs, getCrawlJob, createCrawlJob, listCrawlTemplates, listUrlFindings,
  updateFindingStatus, bulkUpdateFindingStatus, getSiteProfile,
} from "../api.js";
import SiteIntelligence from "./SiteIntelligence.jsx";

const MONITOR_ID = "m1";

beforeEach(() => {
  vi.clearAllMocks();
  listCrawlTemplates.mockResolvedValue({ templates: [] });
  getSiteProfile.mockResolvedValue({ available: false });
});

describe("SiteIntelligence", () => {
  it("no crawl job yet: offers to start one", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [] });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/start a site intelligence crawl/i));
  });

  it("starting a crawl creates a job and shows its progress", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [] });
    const job = {
      id: "j1", phase: "pending", discovered_count: 0, crawled_count: 0, analyzed_count: 0,
      failed_count: 0, skipped_count: 0, ai_calls_total: 0, ai_calls_cached: 0,
      created_at: "2026-10-01T00:00:00Z", started_at: null, finished_at: null, error: null,
    };
    createCrawlJob.mockResolvedValue(job);
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/start a site intelligence crawl/i));
    fireEvent.click(screen.getByText(/^start crawl$/i));
    await waitFor(() => screen.getByText(/queued/i));
    expect(createCrawlJob).toHaveBeenCalledWith(MONITOR_ID);
  });

  it("an active (crawling) job shows progress stats and a cancel button, no findings yet", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [{
      id: "j1", phase: "crawling", discovered_count: 40, crawled_count: 12, analyzed_count: 0,
      failed_count: 1, skipped_count: 3, ai_calls_total: 0, ai_calls_cached: 0,
      created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:01Z", finished_at: null, error: null,
    }] });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/crawling pages/i));
    screen.getByText(/12 crawled/i);
    screen.getByText(/cancel/i);
    expect(listUrlFindings).not.toHaveBeenCalled();
  });

  it("a completed job on the free plan shows a short preview and a locked banner", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [{
      id: "j1", phase: "completed", discovered_count: 20, crawled_count: 20, analyzed_count: 20,
      failed_count: 0, skipped_count: 0, ai_calls_total: 4, ai_calls_cached: 1,
      created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:00Z",
      finished_at: "2026-10-01T00:05:00Z", error: null,
    }] });
    listUrlFindings.mockResolvedValue({
      total: 8, page: 1, page_size: 5, is_pro: false, locked_count: 3,
      findings: Array.from({ length: 5 }, (_, i) => ({
        id: `f${i}`, url: `https://example.com/${i}`, priority: "medium", confidence: "high",
        status: "open", finding_title: `Finding ${i}`, why_it_matters: "It matters.",
        recommended_fix: "Fix it.", affected_url_count: 1, cache_hit: false, model_used: "claude",
      })),
    });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/finding 0/i));
    screen.getByText(/more finding\(s\) on this crawl/i);
    // Free plan never shows the bulk/status mutation controls.
    expect(screen.queryByText(/apply to/i)).toBeNull();
  });

  it("a completed job on Pro shows bulk actions and per-finding status controls", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [{
      id: "j1", phase: "completed", discovered_count: 5, crawled_count: 5, analyzed_count: 5,
      failed_count: 0, skipped_count: 0, ai_calls_total: 2, ai_calls_cached: 0,
      created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:00Z",
      finished_at: "2026-10-01T00:05:00Z", error: null,
    }] });
    listUrlFindings.mockResolvedValue({
      total: 1, page: 1, page_size: 25, is_pro: true, locked_count: 0,
      findings: [{
        id: "f0", url: "https://example.com/a", priority: "critical", confidence: "high",
        status: "open", finding_title: "Missing structured data", why_it_matters: "AI cites structured data.",
        recommended_fix: "Add Product schema.", affected_url_count: 4, cache_hit: false, model_used: "claude",
      }],
    });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/missing structured data/i));
    screen.getByText(/apply to 0 selected/i);
    screen.getByText(/recrawl & verify/i);
    expect(screen.queryByText(/more finding\(s\)/i)).toBeNull();
  });

  it("updating a finding's status calls the API and refreshes", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [{
      id: "j1", phase: "completed", discovered_count: 1, crawled_count: 1, analyzed_count: 1,
      failed_count: 0, skipped_count: 0, ai_calls_total: 1, ai_calls_cached: 0,
      created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:00Z",
      finished_at: "2026-10-01T00:05:00Z", error: null,
    }] });
    const findingsPayload = {
      total: 1, page: 1, page_size: 25, is_pro: true, locked_count: 0,
      findings: [{
        id: "f0", url: "https://example.com/a", priority: "low", confidence: "medium",
        status: "open", finding_title: "Thin content", why_it_matters: "x", recommended_fix: "y",
        affected_url_count: 1, cache_hit: false, model_used: "claude",
      }],
    };
    listUrlFindings.mockResolvedValue(findingsPayload);
    updateFindingStatus.mockResolvedValue({ ...findingsPayload.findings[0], status: "fixed" });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText(/thin content/i));
    fireEvent.change(screen.getByDisplayValue("Open"), { target: { value: "fixed" } });
    await waitFor(() => expect(updateFindingStatus).toHaveBeenCalledWith(MONITOR_ID, "j1", "f0", "fixed"));
  });

  it("a backend error renders a real error message, not a silent blank page", async () => {
    listCrawlJobs.mockRejectedValue(new (await import("../api.js")).ScanError("Could not reach the server.", "network"));
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText("Could not reach the server."));
  });

  it("shows the inferred site profile, clearly labeled with confidence, never silently", async () => {
    listCrawlJobs.mockResolvedValue({ jobs: [{
      id: "j1", phase: "completed", discovered_count: 5, crawled_count: 5, analyzed_count: 5,
      failed_count: 0, skipped_count: 0, ai_calls_total: 1, ai_calls_cached: 0,
      created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:00Z",
      finished_at: "2026-10-01T00:05:00Z", error: null,
    }] });
    listUrlFindings.mockResolvedValue({ total: 0, page: 1, page_size: 25, is_pro: true, locked_count: 0, findings: [] });
    getSiteProfile.mockResolvedValue({
      available: true, pages_analyzed: 5,
      page_type_distribution: { product: 4, other: 1 },
      business_type: { label: "E-commerce / online retail", confidence: "medium",
                       basis: "4 of 5 crawled pages were classified as 'product' pages." },
      entity: { detected_name: "Acme Inc", pages_with_entity_data: 2, consistent: false,
               distinct_names_found: ["Acme Inc", "Acme LLC"], distinct_logos_found: [],
               confidence: "low", basis: "2 of 5 crawled pages carry entity schema." },
    });
    render(<SiteIntelligence monitorId={MONITOR_ID} onBack={() => {}} />);
    await waitFor(() => screen.getByText("About this site"));
    screen.getByText("E-commerce / online retail");
    screen.getByText(/medium confidence/i);
    screen.getByText(/inconsistent entity data/i);
  });
});
