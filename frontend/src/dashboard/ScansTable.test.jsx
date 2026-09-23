/* Recent Scans — the scan-count/quota mismatch bug fix. The backend now marks each
   scan `billable: false` when it was monitor-triggered (never consumes the sidebar's
   "Billable scans" quota, by existing design) — these tests confirm the UI surfaces that
   distinction instead of leaving an unexplained gap between "N scans" and the
   sidebar's quota meter. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ScansTable from "./ScansTable.jsx";

const SCAN = (id, overrides = {}) => ({
  id, url: `https://example.com/${id}`, overall_score: 80, status: "pass",
  scan_time: "2026-01-01T00:00:00Z", duration_ms: 1200, billable: true,
  ...overrides,
});

describe("ScansTable — billable vs. monitor-triggered scans", () => {
  it("shows no quota-explanation banner and no Monitor tag when every scan is billable", () => {
    render(<ScansTable scans={[SCAN("a"), SCAN("b")]} onView={() => {}} onRerun={() => {}} onDelete={() => {}} />);
    expect(screen.queryByText(/counted toward your monthly scan quota/)).toBeNull();
    expect(screen.queryByText("Monitor")).toBeNull();
  });

  it("tags a monitor-triggered (non-billable) scan and explains the count difference", () => {
    render(<ScansTable
      scans={[SCAN("a"), SCAN("b", { billable: false })]}
      onView={() => {}} onRerun={() => {}} onDelete={() => {}} />);
    expect(screen.getByText(/2 scans · 1 counted toward your monthly scan quota/)).toBeTruthy();
    // "Monitor" appears twice by design: once in the explanatory banner's own copy,
    // once as the actual row tag — scope to scan b's own row to confirm the real tag.
    const rows = document.querySelectorAll(".au-sc-table tbody tr");
    const bRow = Array.from(rows).find((r) => r.textContent.includes("example.com/b"));
    expect(bRow.textContent).toContain("Monitor");
    const aRow = Array.from(rows).find((r) => r.textContent.includes("example.com/a"));
    expect(aRow.textContent).not.toContain("Monitor");
  });

  it("never fabricates billable=false — a scan with no explicit field is treated as billable", () => {
    render(<ScansTable scans={[{ id: "x", url: "https://example.com/x", overall_score: 50, status: "warn" }]}
                       onView={() => {}} onRerun={() => {}} onDelete={() => {}} />);
    expect(screen.queryByText(/counted toward your monthly scan quota/)).toBeNull();
    expect(screen.queryByText("Monitor")).toBeNull();
  });
});
