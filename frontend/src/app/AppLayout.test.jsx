/* Global app-shell layout: the sidebar is navigation, so it stays visible while you
   scroll (`position:sticky;top:0;height:100vh` — the standard pattern every
   sidebar-based app uses) instead of vanishing off-screen on a long page. It is pinned
   to exactly ONE viewport, never stretched into a tall empty panel to match a much
   longer main column (a real earlier regression). `overflow-y:auto` is an ordinary
   safety net for the rare case its own content doesn't fit one viewport — it virtually
   never engages, but guarantees "New scan" is never permanently unreachable. The footer
   lives in main's own normal flow (never fixed/sticky, never inside the sidebar) — its
   position was never actually tied to the sidebar's. This file also covers the nav
   active-state fix: "Recent Scans" must not stay highlighted once you've navigated into
   `/app/scans/latest` or a specific scan's detail page. Structural DOM checks only — no
   pixel-position assertions (jsdom does not apply the real stylesheet) — matched by
   static assertions against the actual shipped CSS rules for facts only the CSS itself
   can state. See GlobalLayout.css.test.js for the CSS-only checks shared across all
   three app shells (.au-dash / .au-site / .au-pub). */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(path.join(__dirname, "AppLayout.aurora.css"), "utf8");

vi.mock("../api.js", () => ({
  getDashboard: vi.fn(async () => ({})),
  getScans: vi.fn(async () => []),
  deleteScan: vi.fn(),
  rerunScan: vi.fn(),
  bulkScanUrls: vi.fn(),
  billing: { subscription: vi.fn(async () => ({ plan: "free", usage: null })) },
  startCheckout: vi.fn(),
  ScanError: class ScanError extends Error {},
}));
vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({
    user: { name: "Test User" }, org: { name: "Acme" }, role: "owner",
    logout: vi.fn(), hasPermission: () => true, refreshMe: vi.fn(),
  }),
}));

import AppLayout from "./AppLayout.jsx";

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/app/dashboard"]}>
      <Routes>
        <Route path="/app" element={<AppLayout />}>
          <Route path="dashboard" element={<div data-testid="page-content">PAGE CONTENT</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppLayout — global shell/footer structure", () => {
  it("renders the footer exactly once", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());
    const footers = document.querySelectorAll("footer.au-dash-footer");
    expect(footers.length).toBe(1);
  });

  it("the footer lives inside main's own flow, after the page content — never inside the sidebar", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());

    const main = document.querySelector("main.au-dash-main");
    const side = document.querySelector("aside.au-dash-side");
    expect(main).toBeTruthy();
    const footer = main.querySelector(":scope > footer.au-dash-footer");
    const content = main.querySelector(":scope > .au-dash-content");
    expect(footer).toBeTruthy();
    expect(content).toBeTruthy();
    // DOM order: content, then footer — plain flow, not a portal/overlay.
    expect(content.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Content actually contains the page (footer never sits between/inside content).
    expect(within(content).getByTestId("page-content")).toBeTruthy();
    // The footer is not, and never was, a descendant of the sidebar.
    expect(side.querySelector("footer.au-dash-footer")).toBeNull();
  });

  it("existing sidebar navigation still renders alongside the footer", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());
    const nav = within(document.querySelector("nav.au-dash-nav"));
    expect(nav.getByRole("link", { name: /Dashboard/ })).toBeTruthy();
    expect(nav.getByRole("link", { name: /Action Center/ })).toBeTruthy();
    expect(nav.getByRole("link", { name: /Monitoring/ })).toBeTruthy();
    expect(document.querySelector("aside.au-dash-side")).toBeTruthy();
  });

  it("the footer rule is never position:fixed or position:sticky in the shipped CSS", () => {
    const block = CSS.match(/\.au-dash-footer\{[^}]*\}/)?.[0] || "";
    expect(block).toBeTruthy();
    expect(block).not.toMatch(/position\s*:\s*fixed/);
    expect(block).not.toMatch(/position\s*:\s*sticky/);
  });

  it("the content column can grow before the footer (flex:1 in the shipped CSS)", () => {
    const shellBlock = CSS.match(/\.au-dash\{[^}]*\}/)?.[0] || "";
    const mainBlock = CSS.match(/\.au-dash-main\{[^}]*\}/)?.[0] || "";
    const contentBlock = CSS.match(/\.au-dash-content\{[^}]*\}/)?.[0] || "";
    const footerBlock = CSS.match(/\.au-dash-footer\{[^}]*\}/)?.[0] || "";
    // shell fills at least the viewport — a single page scroll grows it further
    expect(shellBlock).toMatch(/min-height\s*:\s*100vh/);
    // main column is a vertical flex container
    expect(mainBlock).toMatch(/display\s*:\s*flex/);
    expect(mainBlock).toMatch(/flex-direction\s*:\s*column/);
    // content absorbs the leftover space, footer does not grow
    expect(contentBlock).toMatch(/flex\s*:\s*1/);
    expect(footerBlock).toMatch(/flex\s*:\s*none/);
  });

  it("the sidebar stays visible while scrolling (position:sticky), pinned to exactly one viewport — never stretched taller", () => {
    const sideBlock = CSS.match(/\.au-dash-side\{[^}]*\}/)?.[0] || "";
    expect(sideBlock).toMatch(/position\s*:\s*sticky/);
    expect(sideBlock).toMatch(/top\s*:\s*0/);
    expect(sideBlock).toMatch(/height\s*:\s*100vh/);
    // no `align-self` override — it is not stretched to match main's (often much
    // taller) height, so it never renders as a tall empty panel below its real content.
    expect(sideBlock).not.toMatch(/align-self/);
  });

  it("the sidebar has a bounded internal scroll as a safety net (never permanently hides content), and side-foot is pinned near its bottom", () => {
    const sideBlock = CSS.match(/\.au-dash-side\{[^}]*\}/)?.[0] || "";
    const sideFootBlock = CSS.match(/\.au-dash-side-foot\{[^}]*\}/)?.[0] || "";
    expect(sideBlock).toMatch(/overflow-y\s*:\s*auto/);
    expect(sideFootBlock).toMatch(/margin-top\s*:\s*auto/);
  });

  it("the sidebar's own 'New scan' CTA is present in the DOM (not removed/hidden by the fix)", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());
    const side = within(document.querySelector("aside.au-dash-side"));
    expect(side.getByRole("button", { name: /New scan/ })).toBeTruthy();
  });

  it("mobile (<900px): the sidebar drops sticky/viewport-height and becomes a horizontal wrapping bar", () => {
    const mobileBlock = CSS.match(/@media\(max-width:900px\)\{[\s\S]*?\n\}/)?.[0] || "";
    expect(mobileBlock).toMatch(/\.au-dash\{[^}]*grid-template-columns\s*:\s*1fr[^}]*\}/);
    expect(mobileBlock).toMatch(/\.au-dash-side\{[^}]*position\s*:\s*static[^}]*\}/);
    expect(mobileBlock).toMatch(/\.au-dash-side\{[^}]*flex-direction\s*:\s*row[^}]*\}/);
  });

  it('"Recent Scans" is only active on the exact scans list — not on /app/scans/latest or a specific scan\'s detail page', async () => {
    render(
      <MemoryRouter initialEntries={["/app/scans/ba191542-aff4-477d-bf1f-3a4d3a057d86"]}>
        <Routes>
          <Route path="/app" element={<AppLayout />}>
            <Route path="scans/:scanId" element={<div data-testid="page-content">SCAN DETAILS</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());
    const nav = within(document.querySelector("nav.au-dash-nav"));
    const recentScans = nav.getByRole("link", { name: /Recent Scans/ });
    expect(recentScans.className).not.toMatch(/\bon\b/);
  });

  it('"Recent Scans" is still active on its own exact list page (the `end` fix does not break the normal case)', async () => {
    render(
      <MemoryRouter initialEntries={["/app/scans"]}>
        <Routes>
          <Route path="/app" element={<AppLayout />}>
            <Route path="scans" element={<div data-testid="page-content">SCANS LIST</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("page-content")).toBeTruthy());
    const nav = within(document.querySelector("nav.au-dash-nav"));
    const recentScans = nav.getByRole("link", { name: /Recent Scans/ });
    expect(recentScans.className).toMatch(/\bon\b/);
  });
});
