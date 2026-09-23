/* Public shared-report shell (/r/:token) — same global sticky-footer requirement as the
   authenticated app shell: the footer must render once, after the report content, in
   normal document flow. ReportView itself is stubbed (its own behavior is covered by its
   dedicated test suites) so this test stays focused on the shell's structure. */
import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({
  getPublicReport: vi.fn(async () => ({ domain: "example.com" })),
  ScanError: class ScanError extends Error {},
}));
vi.mock("./ReportView.jsx", () => ({
  default: () => <div data-testid="report-body">REPORT</div>,
}));

import PublicReport from "./PublicReport.jsx";

describe("PublicReport — global shell/footer structure", () => {
  it("renders the footer exactly once, after the report content, in normal flow", async () => {
    render(<PublicReport token="tok1" />);
    await waitFor(() => expect(screen.getByTestId("report-body")).toBeTruthy());

    const footers = document.querySelectorAll("footer.au-pub-foot");
    expect(footers.length).toBe(1);

    const shell = document.querySelector(".au-pub");
    const main = shell.querySelector(":scope > main.au-pub-main");
    const footer = shell.querySelector(":scope > footer.au-pub-foot");
    expect(main).toBeTruthy();
    expect(footer).toBeTruthy();
    expect(main.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(main).getByTestId("report-body")).toBeTruthy();
  });
});
