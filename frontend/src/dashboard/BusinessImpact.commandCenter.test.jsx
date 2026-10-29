/* AI Business Impact — Website Intelligence Command Center behavior: per-source
   indicators with honest states, evidence-backed findings with typed statements,
   explicit GA4 website linking, AI referral vs AI answer visibility kept separate,
   website switching, and partial failures. api.js mocked at the boundary. */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

vi.mock("../api.js", () => ({
  listMonitors: vi.fn(), getTrackingStatus: vi.fn(), generateTrackingApiKey: vi.fn(),
  getBusinessImpactSummary: vi.fn(), getBusinessImpactTrafficOverTime: vi.fn(),
  getBusinessImpactChannels: vi.fn(), getBusinessImpactLandingPages: vi.fn(),
  getCommandCenter: vi.fn(), linkGa4Website: vi.fn(),
  ScanError: class ScanError extends Error { constructor(m, c) { super(m); this.code = c; } },
}));
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade: vi.fn() }) }));
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: () => true }) }));

import {
  listMonitors, getTrackingStatus, getBusinessImpactSummary, getBusinessImpactTrafficOverTime,
  getBusinessImpactChannels, getBusinessImpactLandingPages, getCommandCenter, linkGa4Website, ScanError,
} from "../api.js";
import BusinessImpact from "./BusinessImpact.jsx";

const SITES = [{ id: "m1", name: "Acme" }, { id: "m2", name: "Beta" }];
const NC = (reason, source = null) => ({ status: "not_configured", source, as_of: null, reason });

function cc(over = {}) {
  return {
    website: { id: "m1" }, range: { start: "2026-10-02", end: "2026-10-29" },
    indicators: {
      technical: { status: "available", source: "aeomirror_scan", as_of: "2026-10-20T00:00:00",
                   scan_id: "scan-1", score: 41, signals: { pass: 6, warn: 2, fail: 2 } },
      ai_visibility: { status: "available", source: "answer_tracking", as_of: "2026-10-25T00:00:00",
                       mention_rate_pct: 33.3, mentions: 4, samples_analyzed: 12, query_count: 4,
                       providers: ["openai", "perplexity"], link: "/app/answer-tracking/m1/runs/r1" },
      traffic: { status: "available", source: "first_party", as_of: "2026-10-29T00:00:00",
                 current: { total_sessions: 300, ai_sessions: 18 }, previous: { total_sessions: 200 },
                 previous_comparable: true, change_pct: { total_sessions: 50.0, ai_sessions: null } },
      ga4: NC("Google Analytics isn't connected.", "ga4"),
      conversions: { status: "not_configured", configured: false },
      action_center: NC("No completed site crawl yet.", "url_intelligence"),
      ...over.indicators,
    },
    findings: over.findings ?? [{
      id: "tech:1", category: "technical_seo", title: "Structured data: Missing schema",
      priority: "high", priority_rationale: "Audit rated this Critical.",
      statements: [
        { type: "observed", text: "The 2026-10-20 audit scored Structured data 10/100 (fail)." },
        { type: "recommendation", text: "Add Organization schema." },
      ],
      evidence: [{ label: "Signal score", value: "10/100", source: "aeomirror_scan" }],
      sample: null, confidence: "high", date_range: { as_of: "2026-10-20" }, link: "/app/scans/scan-1",
    }],
  };
}

const SUMMARY = {
  total_sessions: 300, ai_sessions: 18, ai_share_pct: 6.0, unique_visitors: 210, total_page_views: 900,
  ai_unique_visitors: 15, ai_conversions: 0, ai_conversion_rate_pct: 0, ai_revenue_cents: 0,
  confirmed_bookings: 0, leads: 0, has_data: true, is_pro: true, ga4_connected: false,
  conversion_tracking_configured: false,
};

function renderAt(path = "/app/business-impact") {
  return render(<MemoryRouter initialEntries={[path]}><BusinessImpact /></MemoryRouter>);
}

beforeEach(() => {
  vi.resetAllMocks();
  listMonitors.mockResolvedValue({ monitors: SITES });
  getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true, has_tracking_api_key: false });
  getBusinessImpactSummary.mockResolvedValue(SUMMARY);
  getBusinessImpactTrafficOverTime.mockResolvedValue({ series: [] });
  getBusinessImpactChannels.mockResolvedValue({ channels: [] });
  getBusinessImpactLandingPages.mockResolvedValue({ pages: [] });
  getCommandCenter.mockResolvedValue(cc());
});

describe("Indicators", () => {
  it("shows each source separately with its status, value and source label", async () => {
    renderAt();
    await waitFor(() => screen.getByText("Technical audit"));
    const tech = screen.getByText("Technical audit").closest(".sp-bi-ind");
    within(tech).getByText("41/100");
    within(tech).getByText("Current");
    within(tech).getByText(/AEOMirror audit/);
    const vis = screen.getByText("AI answer visibility", { selector: ".sp-bi-ind-title" }).closest(".sp-bi-ind");
    within(vis).getByText("33.3%");
    within(vis).getByText(/12 answers to 4 tracked queries/);
  });

  it("an unconfigured source shows its state and reason, never a 0", async () => {
    renderAt();
    await waitFor(() => screen.getByText("Site crawl findings"));
    const crawl = screen.getByText("Site crawl findings").closest(".sp-bi-ind");
    within(crawl).getByText("Not set up");
    within(crawl).getByText("—");
    within(crawl).getByText(/no completed site crawl yet/i);
    expect(within(crawl).queryByText("0")).toBeNull();
  });
});

describe("Findings", () => {
  it("lists findings with priority and expands to typed statements, evidence and a link", async () => {
    renderAt();
    await waitFor(() => screen.getByText("Structured data: Missing schema"));
    fireEvent.click(screen.getByText("Structured data: Missing schema"));
    screen.getByText("Observed");
    screen.getByText("Next step");
    screen.getByText("10/100");
    screen.getByText(/Audit rated this Critical/);
    expect(screen.getByRole("link", { name: /open the evidence/i }).getAttribute("href")).toBe("/app/scans/scan-1");
  });

  it("with no evidence, says so instead of inventing a finding", async () => {
    getCommandCenter.mockResolvedValue(cc({ findings: [] }));
    renderAt();
    await waitFor(() => screen.getByText(/no evidence-backed findings for this website yet/i));
  });
});

describe("Honest states", () => {
  it("conversion figures read 'Not measured yet' when conversion tracking isn't configured — never $0.00", async () => {
    renderAt();
    await waitFor(() => screen.getByText("Confirmed conversions from AI"));
    expect(screen.getAllByText("Not measured yet").length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText("$0.00")).toBeNull();
  });

  it("shows a previous-period change only when the backend says the periods are comparable", async () => {
    renderAt();
    await waitFor(() => screen.getByText("+50% vs previous"));
    getCommandCenter.mockResolvedValue(cc({ indicators: { traffic: {
      status: "available", source: "first_party", current: { total_sessions: 300, ai_sessions: 18 },
      previous: null, previous_comparable: false, change_pct: null } } }));
    fireEvent.click(screen.getByText("7 days"));
    await waitFor(() => expect(screen.queryByText(/vs previous/)).toBeNull());
  });
});

describe("AI search intelligence", () => {
  it("keeps AI referral traffic and AI answer visibility as separate panels", async () => {
    renderAt();
    await waitFor(() => screen.getByText("AI referral traffic"));
    const ref = screen.getByText("AI referral traffic").closest(".sp-bi-ai-col");
    const vis = screen.getByText("AI answer visibility", { selector: "h3" }).closest(".sp-bi-ai-col");
    within(ref).getByText("18");
    within(vis).getByText("33.3%");
    expect(within(ref).queryByText("33.3%")).toBeNull();
    within(ref).getByText(/google organic search — are not counted here/i);
  });
});

describe("GA4 website mapping", () => {
  it("offers an explicit link when the GA4 property isn't linked, and sends this website's id", async () => {
    getCommandCenter.mockResolvedValue(cc({ indicators: { ga4: {
      status: "not_configured", source: "ga4", link_required: true, property_name: "Acme GA4",
      reason: "GA4 property “Acme GA4” isn't linked to a website." } } }));
    linkGa4Website.mockResolvedValue({ status: "ok", linked_monitor_id: "m1" });
    renderAt();
    await waitFor(() => screen.getByText("Link to this website"));
    fireEvent.click(screen.getByText("Link to this website"));
    await waitFor(() => expect(linkGa4Website).toHaveBeenCalledWith("m1"));
  });
});

describe("Multi-website", () => {
  it("switching website refetches every section for the new website only", async () => {
    renderAt();
    await waitFor(() => screen.getByText("Technical audit"));
    fireEvent.change(screen.getByLabelText("Website"), { target: { value: "m2" } });
    await waitFor(() => expect(getCommandCenter).toHaveBeenLastCalledWith(expect.objectContaining({ monitorId: "m2" })));
    expect(getTrackingStatus).toHaveBeenLastCalledWith("m2");
    expect(getBusinessImpactSummary).toHaveBeenLastCalledWith(expect.objectContaining({ monitorId: "m2" }));
  });

  it("opens the website named in ?site= and ignores one that isn't in this organization", async () => {
    renderAt("/app/business-impact?site=m2");
    await waitFor(() => expect(getCommandCenter).toHaveBeenCalledWith(expect.objectContaining({ monitorId: "m2" })));
    vi.clearAllMocks();
    listMonitors.mockResolvedValue({ monitors: SITES });
    getTrackingStatus.mockResolvedValue({ site_id: "m1", installed: true });
    getBusinessImpactSummary.mockResolvedValue(SUMMARY);
    getBusinessImpactTrafficOverTime.mockResolvedValue({ series: [] });
    getCommandCenter.mockResolvedValue(cc());
    renderAt("/app/business-impact?site=someone-elses-site");
    await waitFor(() => expect(getCommandCenter).toHaveBeenCalled());
    expect(getCommandCenter).not.toHaveBeenCalledWith(expect.objectContaining({ monitorId: "someone-elses-site" }));
  });
});

describe("Partial failure", () => {
  it("a failed command-center request doesn't hide the measured traffic sections", async () => {
    getCommandCenter.mockRejectedValue(new ScanError("Could not load this website's intelligence summary.", 500));
    renderAt();
    await waitFor(() => screen.getByText(/could not load this website's intelligence summary/i));
    screen.getByText("Website sessions");
    screen.getByText("Traffic over time");
  });
});

describe("Traffic chart", () => {
  it("states how many days were tracked and that earlier days are blank, not zero", async () => {
    getBusinessImpactTrafficOverTime.mockResolvedValue({ series: [
      { date: "2026-10-27", by_channel: {}, tracked: false, total: null },
      { date: "2026-10-28", by_channel: { direct: 3 }, tracked: true, total: 3 },
      { date: "2026-10-29", by_channel: {}, tracked: true, total: 0 },
    ] });
    renderAt();
    await waitFor(() => screen.getByText(/3 sessions over 2 tracked days/));
    screen.getByText(/1 earlier day before tracking started are left blank/);
  });
});

describe("Provenance and qualified values", () => {
  it("a partial GA4 value is shown with the reason it's partial", async () => {
    getCommandCenter.mockResolvedValue(cc({ indicators: { ga4: {
      status: "partial", source: "ga4", as_of: "2026-10-29T00:00:00", sessions: 120, ai_assistant_sessions: 4,
      reason: "GA4 history starts 2026-10-20; earlier days in this period aren't included." } } }));
    renderAt();
    await waitFor(() => screen.getByText("Google Analytics"));
    const g = screen.getByText("Google Analytics").closest(".sp-bi-ind");
    within(g).getByText("120");
    within(g).getByText("Partial");
    within(g).getByText(/earlier days in this period aren't included/);
  });

  it("explains why no percentage change is shown", async () => {
    getCommandCenter.mockResolvedValue(cc({ indicators: { traffic: {
      status: "available", source: "first_party", current: { total_sessions: 300, ai_sessions: 18 },
      previous: { total_sessions: 0 }, previous_comparable: true, change_pct: { total_sessions: null },
      change_note: "The previous period had 0 sessions, so a percentage change can't be calculated." } } }));
    renderAt();
    await waitFor(() => screen.getByText(/percentage change can't be calculated/));
  });

  it("shows a finding's formula with its inputs and labels unknowns as such", async () => {
    getCommandCenter.mockResolvedValue(cc({ findings: [{
      id: "t1", category: "acquisition_engagement", title: "Website sessions down 50.0% vs the previous period",
      priority: "high", priority_rationale: "Declines of 30%+ rank high.",
      statements: [
        { type: "observed", text: "20 sessions this period; 40 in the previous equal-length period." },
        { type: "calculated", text: "Change = (20 − 40) ÷ 40 = -50.0%." },
        { type: "unavailable", text: "Conversions aren't measured, so business impact is unknown." },
      ],
      evidence: [{ label: "Sessions (current / previous)", value: "20 / 40", source: "first_party" }],
      calculation: { formula: "pct_change", expression: "(current − previous) ÷ previous × 100",
                     inputs: { current: 20, previous: 40 }, result: -50.0 },
      sample: { size: 60, unit: "sessions" }, confidence: "medium", date_range: null, link: null,
    }] }));
    renderAt();
    await waitFor(() => screen.getByText(/sessions down 50.0%/));
    fireEvent.click(screen.getByText(/sessions down 50.0%/));
    screen.getByText(/Formula: \(current − previous\) ÷ previous × 100 \(current = 20, previous = 40 → -50\)/);
    screen.getByText("Unknown");
  });
});

describe("Website switching races", () => {
  it("a slow response for the previous website never appears under the newly selected one", async () => {
    let resolveA;
    getCommandCenter.mockImplementationOnce(() => new Promise((r) => { resolveA = r; }));     // m1, slow
    renderAt();
    await waitFor(() => expect(getCommandCenter).toHaveBeenCalledTimes(1));
    getCommandCenter.mockResolvedValue(cc({ website: { id: "m2" }, findings: [{
      ...cc().findings[0], id: "b1", title: "Beta finding" }] }));
    fireEvent.change(screen.getByLabelText("Website"), { target: { value: "m2" } });
    await waitFor(() => screen.getByText("Beta finding"));
    resolveA(cc({ findings: [{ ...cc().findings[0], id: "a1", title: "Acme finding" }] }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Acme finding")).toBeNull();
    screen.getByText("Beta finding");
  });
});
