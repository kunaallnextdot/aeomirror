/* Scan Details — Problem/Evidence/Why/Fix integration. Verifies the signal accordion
   renders the SAME build_recommendations()/fix_template data ReportView's Recommendations
   section uses (via the extracted <RecommendationCard>, reused not re-implemented), while
   its own native issues/evidence stay exactly as before. api.js mocked at the boundary. */
import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  getScanStatus: vi.fn(),
  getReport: vi.fn(),
  createVerification: vi.fn(),
  getVerifications: vi.fn(async () => ({ verifications: [] })),
  getContentInsights: vi.fn(async () => ({ insights: [] })),
  analyzeContent: vi.fn(),
  ScanError: class ScanError extends Error {
    constructor(message, code) { super(message); this.code = code; }
  },
}));
const DEFAULT_UPGRADE = { openUpgrade: vi.fn(), isLimited: false, handleGated: () => false };
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: vi.fn(() => DEFAULT_UPGRADE) }));

import { getReport, createVerification, getVerifications, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";
import ScanDetails from "./ScanDetails.jsx";

const SCAN = (sections) => ({
  scan_id: "s1", domain: "example.com", url: "https://example.com/",
  overall_score: 62, scanner_version: "3.0.0", rubric_version: "2026.07.1",
  scanned_at: "2026-01-01T00:00:00Z", duration_ms: 4200, status: "completed",
  bulk: null, sections,
});

// For Fix Verification's scan-picker: the baseline itself PLUS one later, same-domain
// candidate scan. Matched by `id`/`domain`/`scan_time`, same shape getScans() returns.
const SCANS_LIST = [
  { id: "s1", domain: "example.com", scan_time: "2026-01-01T00:00:00Z", overall_score: 42, status: "completed" },
  { id: "s2", domain: "example.com", scan_time: "2026-01-05T00:00:00Z", overall_score: 91, status: "completed" },
];

const FRESHNESS_FAIL = {
  id: "freshness", label: "Freshness", score: 40, status: "fail",
  issues: ["Important content appears outdated."],
  recommendations: ["Review and update stale content."],
  evidence: { last_modified: "2025-01-01" },
};
const LINKS_WARN = {
  id: "links", label: "Internal Links", score: 62, status: "warn",
  issues: ["Important pages have limited internal support."],
  recommendations: ["Add contextual internal links."],
  evidence: { weak_pages: 3 },
};
const SITEMAP_PASS = {
  id: "sitemap", label: "XML Sitemap", score: 100, status: "pass",
  issues: [], recommendations: [], evidence: { sitemap_url: "https://example.com/sitemap.xml" },
};

const REC = (id, overrides = {}) => ({
  id, issue_title: "Fix it", signal_label: id, category: "AI Extractability",
  severity: "High", priority: "High", priority_score: 2, score: 40, weight: 10,
  status: "fail", description: `Diagnosis for ${id}.`,
  business_impact: "Reduces trust.", ai_visibility_impact: "Lower answer confidence.",
  estimated_fix_time: "30m", difficulty: "Easy",
  fix_template: {
    problem: `Problem for ${id}.`,
    explanation: `Explanation for ${id}.`,
    recommended_fix: [`Recommended step for ${id}.`],
    implementation_example: `Implementation example for ${id}.`,
    expected_outcome: `Expected outcome for ${id}.`,
  },
  ...overrides,
});

const REPORT = (recommendations, extra = {}) => ({
  domain: "example.com", recommendations, recommendation_count: recommendations.length,
  locked_recommendation_count: 0, recommendations_preview: false, ai: null, ...extra,
});

// A signal with real issues/recommendations is now expanded by DEFAULT (problem-first
// landing) — this only clicks to open when it isn't already, so it works the same
// whether the signal auto-expanded or not, without ever accidentally toggling it shut.
async function openSignal(label) {
  const head = screen.getByText(label).closest(".au-sd-sig-head");
  if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
  return head;
}

describe("ScanDetails — Problem/Evidence/Why/Fix", () => {
  it("FAIL signal renders Problem, real Evidence, Why (Explanation), Recommended fix, Implementation and Expected outcome", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness")]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Freshness");

    expect(screen.getByText("Important content appears outdated.")).toBeTruthy();   // native issue
    expect(screen.getByText("last_modified")).toBeTruthy();                          // native evidence key
    expect(screen.getByText("2025-01-01")).toBeTruthy();                             // native evidence value

    await waitFor(() => expect(screen.getByText("Explanation for freshness.")).toBeTruthy());
    expect(screen.getByText("Recommended step for freshness.")).toBeTruthy();
    expect(screen.getByText("Implementation example for freshness.")).toBeTruthy();
    expect(screen.getByText("Expected outcome for freshness.")).toBeTruthy();

    // the plain scanner recommendation list is NOT also shown once the rich fix_template
    // is available — never two sources of "how to fix it" for the same signal.
    expect(screen.queryByText("Review and update stale content.")).toBeNull();
  });

  it("Phase H: composite render order is Problem -> Fix -> Evidence, not Problem -> Evidence -> Fix", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness")]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Freshness");
    await waitFor(() => expect(screen.getByText("Recommended step for freshness.")).toBeTruthy());

    const problem = screen.getByText("Important content appears outdated.");   // native "What's wrong"
    const fix = screen.getByText("Recommended step for freshness.");           // RecommendationCard's Fix step
    const evidence = screen.getByText("last_modified");                        // native Evidence block

    // problem comes before fix, and fix comes before evidence — never evidence before the fix
    expect(problem.compareDocumentPosition(fix) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(fix.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("never shows a duplicate 'Evidence' heading from RecommendationCard — Scan Details' own native evidence block is the only one", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness", {
      evidence: { issues: ["Important content appears outdated."], findings: { last_modified: "2025-01-01" } },
    })]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    const head = await openSignal("Freshness");
    await waitFor(() => expect(screen.getByText("Explanation for freshness.")).toBeTruthy());
    // exactly ONE "Evidence" heading in the whole open signal panel — Scan Details'
    // own native block (already asserted above via "last_modified"/"2025-01-01") —
    // never a second one from RecommendationCard (showProblem=false here).
    const body = head.parentElement.querySelector(".au-sd-sig-body");
    expect(within(body).getAllByText("Evidence").length).toBe(1);
  });

  it("WARN signal uses the exact same Problem/Evidence/Why/Fix structure as FAIL", async () => {
    getReport.mockResolvedValue(REPORT([REC("links")]));
    render(<ScanDetails scan={SCAN([LINKS_WARN])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Internal Links");

    expect(screen.getByText("Important pages have limited internal support.")).toBeTruthy();
    expect(screen.getByText("weak_pages")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Explanation for links.")).toBeTruthy());
    expect(screen.getByText("Recommended step for links.")).toBeTruthy();
    expect(screen.getByText("Expected outcome for links.")).toBeTruthy();
  });

  it("PASS signal stays concise — no fix card, no fabricated recommendation", async () => {
    getReport.mockResolvedValue(REPORT([]));   // build_recommendations() returns none for a clean pass
    render(<ScanDetails scan={SCAN([SITEMAP_PASS])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("XML Sitemap");

    expect(screen.getByText("No issues found.")).toBeTruthy();
    expect(screen.getByText("https://example.com/sitemap.xml")).toBeTruthy();   // real evidence still shown
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    expect(screen.queryByText(/Recommended fix/)).toBeNull();
    expect(screen.queryByText(/Implementation example/)).toBeNull();
    expect(screen.queryByText(/Expected outcome/)).toBeNull();
  });

  it("no recommendation available does not fabricate a fix", async () => {
    const noRecSignal = { id: "schema", label: "Schema", score: 55, status: "warn",
      issues: ["No Organization schema detected."], recommendations: [], evidence: {} };
    getReport.mockResolvedValue(REPORT([]));   // no matching recommendation for this signal
    render(<ScanDetails scan={SCAN([noRecSignal])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Schema");

    expect(screen.getByText("No Organization schema detected.")).toBeTruthy();
    expect(screen.getByText("Evidence unavailable for this check.")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(
      "This check has no implementation example — see the issue above for what to address.")).toBeTruthy());
    expect(screen.queryByText(/Recommended fix/)).toBeNull();
  });

  it("multiple recommendations map to the correct signal — no cross-contamination", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness"), REC("links")]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL, LINKS_WARN])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Freshness");
    await openSignal("Internal Links");

    await waitFor(() => expect(screen.getByText("Explanation for freshness.")).toBeTruthy());
    expect(screen.getByText("Explanation for links.")).toBeTruthy();
    // each signal's own fix text appears exactly once — not duplicated across cards
    expect(screen.getAllByText("Recommended step for freshness.").length).toBe(1);
    expect(screen.getAllByText("Recommended step for links.").length).toBe(1);
  });

  it("evidence is real server data, rendered independently of (and before) the report fetch resolving", async () => {
    let resolveReport;
    getReport.mockReturnValue(new Promise((res) => { resolveReport = res; }));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Freshness");

    // native evidence/issue text is present immediately, before the report call resolves
    expect(screen.getByText("last_modified")).toBeTruthy();
    expect(screen.getByText("Important content appears outdated.")).toBeTruthy();
    expect(screen.queryByText(/Recommended fix/)).toBeNull();

    resolveReport(REPORT([REC("freshness")]));
    await waitFor(() => expect(screen.getByText("Recommended step for freshness.")).toBeTruthy());
  });

  it("Free/Pro gating: a locked-fix banner appears only when the report reports locked recommendations", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness")], { locked_recommendation_count: 3 }));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await waitFor(() => expect(screen.getByText(/3 more detailed fixes available/)).toBeTruthy());
    expect(screen.getByText("Unlock")).toBeTruthy();
  });

  it("no locked-fix banner when nothing is locked", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness")]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    expect(screen.queryByText(/more detailed fixes available/)).toBeNull();
  });

  it("never shows an affected-pages action for a single-page scan (no such data exists)", async () => {
    getReport.mockResolvedValue(REPORT([REC("freshness")]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL])} onBack={() => {}} onRerun={() => {}} />);
    await openSignal("Freshness");
    await waitFor(() => expect(screen.getByText("Recommended step for freshness.")).toBeTruthy());
    expect(screen.queryByText(/View affected pages/)).toBeNull();
  });

  it("existing accordion expand/collapse behavior is unchanged", async () => {
    getReport.mockResolvedValue(REPORT([]));
    render(<ScanDetails scan={SCAN([SITEMAP_PASS])} onBack={() => {}} onRerun={() => {}} />);
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    const head = screen.getByText("XML Sitemap").closest(".au-sd-sig-head");
    expect(head.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(head);
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("No issues found.")).toBeTruthy();
    fireEvent.click(head);
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("No issues found.")).toBeNull();
  });

  it("problem-first landing: a signal with a real issue is expanded by default; a clean signal stays collapsed", async () => {
    getReport.mockResolvedValue(REPORT([]));
    render(<ScanDetails scan={SCAN([FRESHNESS_FAIL, SITEMAP_PASS])} onBack={() => {}} onRerun={() => {}} />);
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    expect(screen.getByText("Freshness").closest(".au-sd-sig-head").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Important content appears outdated.")).toBeTruthy();   // visible with no click
    expect(screen.getByText("XML Sitemap").closest(".au-sd-sig-head").getAttribute("aria-expanded")).toBe("false");
  });

  it("does not fetch the report for a bulk scan (no signal-analysis panel to enrich)", async () => {
    const callsBefore = getReport.mock.calls.length;   // other tests in this file also call it
    const bulkScan = { ...SCAN([]), sections: undefined,
      bulk: { avg_score: 70, page_count: 2, pages: [], requested: 2 } };
    render(<ScanDetails scan={bulkScan} onBack={() => {}} onRerun={() => {}} />);
    await waitFor(() => expect(screen.getByText("Pages scanned")).toBeTruthy());
    expect(getReport.mock.calls.length).toBe(callsBefore);
  });
});

describe("ScanDetails — Fix Verification V2 (persisted history, SCAN -> FIX -> RE-SCAN -> VERIFY -> PERSIST)", () => {
  const FAIL_SIGNAL = { id: "schema", label: "Schema", score: 42, status: "fail",
    issues: ["Organization schema missing"], recommendations: [], evidence: { organization_entity: "not_detected" } };

  const VERIFICATION = (over = {}) => {
    const base = {
      id: "v1", baseline_scan_id: "s1", verification_scan_id: "s2", signal_id: "schema",
      verification_status: "verified", status_before: "fail", status_after: "pass",
      score_before: 42, score_after: 91, resolved_issues: ["Organization schema missing"],
      remaining_issues: [], new_issues: [], evidence_changes: [], created_at: "2026-01-05T00:00:00Z",
      ...over,
    };
    // Mirrors the real API's _serialize_verification(): score_delta is always the
    // server-computed derived value, never a field the caller sets directly.
    return { ...base, score_delta: Math.round((base.score_after - base.score_before) * 10) / 10 };
  };

  async function openVerifyPanel(sections = [FAIL_SIGNAL], scansList = SCANS_LIST) {
    getReport.mockResolvedValue({ recommendations: [] });
    render(<ScanDetails scan={SCAN(sections)} scans={scansList} onBack={() => {}} onRerun={() => {}} canRun />);
    // FAIL_SIGNAL has real issues, so it's already expanded by default — only click
    // to open if it genuinely isn't (never toggle an already-open signal shut).
    const head = screen.getByText("Schema").closest(".au-sd-sig-head");
    if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
    fireEvent.click(screen.getByText("Verify after re-scan"));
  }

  it("no verification record and no later scan: shows the implement-then-rescan CTA, never claims 'Verified'", async () => {
    getVerifications.mockResolvedValue({ verifications: [] });
    await openVerifyPanel([FAIL_SIGNAL], [SCANS_LIST[0]]);   // only the baseline itself exists
    expect(screen.getByText(/Re-scan your website after implementing this fix/)).toBeTruthy();
    expect(screen.getByText("Run re-scan")).toBeTruthy();
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
  });

  it("no verification record, but a later scan exists: offers the picker, claims nothing", async () => {
    getVerifications.mockResolvedValue({ verifications: [] });
    await openVerifyPanel();
    expect(screen.getByLabelText("Pick a scan to verify Schema against")).toBeTruthy();
    expect(screen.getByText("Verify")).toBeTruthy();
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
    expect(screen.queryByText("Partially improved")).toBeNull();
  });

  it("reload persistence: a persisted VERIFIED record is shown immediately, with no click required", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION()] });
    await openVerifyPanel();   // openVerifyPanel only expands the panel — never clicks Verify
    await waitFor(() => expect(screen.getByText("Verified after re-scan")).toBeTruthy());
    expect(screen.getByText("FAIL · 42")).toBeTruthy();
    expect(screen.getByText("PASS · 91")).toBeTruthy();
    expect(screen.getByText("Score change: +49")).toBeTruthy();
    const resultBox = document.querySelector(".au-sd-verify-result");
    expect(within(resultBox).getByText("Organization schema missing")).toBeTruthy();
    expect(getVerifications).toHaveBeenCalledWith("s1");
  });

  it("a persisted PARTIALLY_IMPROVED record shows on load", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION({
      verification_status: "partially_improved", status_after: "warn", score_after: 65,
      resolved_issues: [], remaining_issues: ["Organization schema missing"],
    })] });
    await openVerifyPanel();
    // "Partially improved" appears twice on screen by design: the compact header
    // badge (always visible once data loads) and the full panel's own title — scope
    // to the panel to avoid that expected duplication.
    await waitFor(() => expect(document.querySelector(".au-sd-verify-result")).toBeTruthy());
    expect(within(document.querySelector(".au-sd-verify-result")).getByText("Partially improved")).toBeTruthy();
    expect(screen.getByText("Still needs attention")).toBeTruthy();
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
  });

  it("a persisted UNCHANGED record shows on load — a small score wobble is not a successful fix", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION({
      verification_status: "unchanged", status_after: "fail", score_before: 42, score_after: 43,
      resolved_issues: [], remaining_issues: ["Organization schema missing"],
    })] });
    await openVerifyPanel();
    await waitFor(() => expect(screen.getByText("No meaningful change detected")).toBeTruthy());
    expect(screen.getByText("Still needs attention")).toBeTruthy();
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
    expect(screen.queryByText("Partially improved")).toBeNull();
  });

  it("a persisted REGRESSED record shows on load", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION({
      verification_status: "regressed", status_before: "warn", status_after: "fail",
      score_before: 65, score_after: 20, resolved_issues: [],
      remaining_issues: ["Organization schema missing"],
    })] });
    await openVerifyPanel([{ ...FAIL_SIGNAL, status: "warn", score: 65 }]);
    await waitFor(() => expect(screen.getByText("Regression detected")).toBeTruthy());
    expect(screen.getByText("WARN · 65")).toBeTruthy();
    expect(screen.getByText("FAIL · 20")).toBeTruthy();
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
  });

  it("a persisted record's new issues are shown without blaming the fix", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION({
      verification_status: "regressed", status_after: "fail", score_after: 20,
      resolved_issues: [], remaining_issues: ["Organization schema missing"],
      new_issues: ["Invalid JSON-LD syntax"],
    })] });
    await openVerifyPanel();
    await waitFor(() => expect(screen.getByText("New issue detected in the verification scan")).toBeTruthy());
    expect(screen.getByText("Invalid JSON-LD syntax")).toBeTruthy();
  });

  it("a persisted record's evidence changes are shown, never fabricated", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION({
      evidence_changes: [{ key: "organization_entity", before: "not_detected", after: "detected" }],
    })] });
    await openVerifyPanel();
    await waitFor(() => expect(document.querySelector(".au-sd-verify-result")).toBeTruthy());
    const resultBox = within(document.querySelector(".au-sd-verify-result"));
    expect(resultBox.getByText("organization_entity")).toBeTruthy();
    expect(resultBox.getByText("not_detected → detected")).toBeTruthy();
  });

  it("clicking Verify calls the server-authoritative endpoint and reloads the persisted result", async () => {
    getVerifications
      .mockResolvedValueOnce({ verifications: [] })                    // initial load: nothing yet
      .mockResolvedValueOnce({ verifications: [VERIFICATION()] });     // reload after create
    createVerification.mockResolvedValue(VERIFICATION());
    await openVerifyPanel();
    fireEvent.click(screen.getByText("Verify"));
    expect(createVerification).toHaveBeenCalledWith(
      { baselineScanId: "s1", verificationScanId: "s2", signalId: "schema" });
    await waitFor(() => expect(screen.getByText("Verified after re-scan")).toBeTruthy());
  });

  it("not comparable (422): shows the exact fallback message, never a fabricated result", async () => {
    getVerifications.mockResolvedValue({ verifications: [] });
    createVerification.mockRejectedValue(new ScanError("nope", 422));
    await openVerifyPanel();
    fireEvent.click(screen.getByText("Verify"));
    await waitFor(() => expect(screen.getByText(/cannot be compared reliably/)).toBeTruthy());
  });

  it("Phase 8 — verification history: only shown with more than one record, each a real persisted row", async () => {
    getVerifications.mockResolvedValue({ verifications: [
      VERIFICATION({ id: "v2", created_at: "2026-01-10T00:00:00Z", score_after: 91 }),
      VERIFICATION({ id: "v1", created_at: "2026-01-05T00:00:00Z", verification_status: "partially_improved", score_after: 65 }),
    ] });
    await openVerifyPanel();
    await waitFor(() => expect(screen.getByText("Verification history")).toBeTruthy());
    expect(screen.getAllByText(/42 →/).length).toBeGreaterThan(0);
  });

  it("no history section when only one record exists", async () => {
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION()] });
    await openVerifyPanel();
    await waitFor(() => expect(screen.getByText("Verified after re-scan")).toBeTruthy());
    expect(screen.queryByText("Verification history")).toBeNull();
  });

  it("accessibility: toggle has aria-expanded, picker has an accessible label", async () => {
    getReport.mockResolvedValue({ recommendations: [] });
    getVerifications.mockResolvedValue({ verifications: [] });
    render(<ScanDetails scan={SCAN([FAIL_SIGNAL])} scans={SCANS_LIST} onBack={() => {}} onRerun={() => {}} canRun />);
    // FAIL_SIGNAL has a real issue, so it's already expanded by default — no click needed.
    const toggle = screen.getByText("Verify after re-scan");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByLabelText("Pick a scan to verify Schema against")).toBeTruthy();
  });

  it("the signal header shows a compact verification badge as soon as a record exists, without expanding", async () => {
    getReport.mockResolvedValue({ recommendations: [] });
    getVerifications.mockResolvedValue({ verifications: [VERIFICATION()] });
    render(<ScanDetails scan={SCAN([FAIL_SIGNAL])} scans={SCANS_LIST} onBack={() => {}} onRerun={() => {}} canRun />);
    await waitFor(() => expect(getVerifications).toHaveBeenCalled());
    const head = screen.getByText("Schema").closest(".au-sd-sig-head");
    expect(within(head).getByText("Verified")).toBeTruthy();
  });

  it("a clean/passing signal never offers Fix Verification (nothing to verify)", async () => {
    const passSignal = { id: "sitemap", label: "XML Sitemap", score: 100, status: "pass", issues: [], recommendations: [], evidence: {} };
    getReport.mockResolvedValue({ recommendations: [] });
    getVerifications.mockResolvedValue({ verifications: [] });
    render(<ScanDetails scan={SCAN([passSignal])} scans={SCANS_LIST} onBack={() => {}} onRerun={() => {}} canRun />);
    fireEvent.click(screen.getByText("XML Sitemap").closest(".au-sd-sig-head"));
    await waitFor(() => expect(getReport).toHaveBeenCalled());
    expect(screen.queryByText("Verify after re-scan")).toBeNull();
  });

  it("Free/Pro: a metered-quota (402) gate is handled via the existing upgrade flow, not a generic error", async () => {
    useUpgrade.mockReturnValue({ openUpgrade: vi.fn(), isLimited: true, handleGated: () => true });
    getVerifications.mockResolvedValue({ verifications: [] });
    createVerification.mockRejectedValue(new ScanError("Free plan includes 3 comparisons per month.", 402));
    await openVerifyPanel();
    fireEvent.click(screen.getByText("Verify"));
    await waitFor(() => expect(createVerification).toHaveBeenCalled());
    expect(screen.queryByText("Could not verify.")).toBeNull();
    useUpgrade.mockReturnValue(DEFAULT_UPGRADE);   // restore for any later test in this file
  });

  it("a real (non-gated) verify failure shows an honest error, not a fake result", async () => {
    getVerifications.mockResolvedValue({ verifications: [] });
    createVerification.mockRejectedValue(new ScanError("Network error."));
    await openVerifyPanel();
    fireEvent.click(screen.getByText("Verify"));
    await waitFor(() => expect(screen.getByText("Network error.")).toBeTruthy());
    expect(screen.queryByText("Verified after re-scan")).toBeNull();
  });
});
