/* Privacy Policy (/privacy) + Terms of Service (/terms) — pure rendering tests, same
   isolation convention as PublicReport.test.jsx (render the leaf component directly;
   neither page calls an API or useAuth(), so no mocking is needed). */
import React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";

import PrivacyPolicy from "./PrivacyPolicy.jsx";
import TermsOfService from "./TermsOfService.jsx";

afterEach(cleanup);

describe("PrivacyPolicy", () => {
  it("renders the page heading and sets document.title", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("heading", { name: "Privacy Policy", level: 1 });
    expect(document.title).toBe("Privacy Policy · AEOMirror");
  });

  it("documents the GA4 integration honestly: read-only scope, encrypted refresh token, no access-token storage", () => {
    render(<PrivacyPolicy />);
    screen.getByText(/read-only/i);
    screen.getByText(/encrypted before it is stored/i);
    screen.getByText(/never store your google access token/i);
  });

  it("documents the AI Overview attribution limitation — never claims precise identification", () => {
    render(<PrivacyPolicy />);
    screen.getByText(/does not provide any way to separately identify/i);
    screen.getByText(/"Unattributable," never presented as confirmed AI traffic/);
  });

  it("includes a real, working support contact — never a placeholder", () => {
    render(<PrivacyPolicy />);
    const links = screen.getAllByRole("link", { name: /aeomirror\.support@gmail\.com/i });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0].getAttribute("href")).toBe("mailto:aeomirror.support@gmail.com");
  });

  it("never makes a blanket 'we never store/share any data' claim (narrow, accurate claims like 'never store your password in plain text' are fine)", () => {
    const { container } = render(<PrivacyPolicy />);
    const text = container.textContent.toLowerCase();
    expect(text).not.toMatch(/we never store (any|your) data\b/);
    expect(text).not.toMatch(/we never share (any|your) data\b/);
    expect(text).not.toMatch(/automatically delete(d)? all/);
  });
});

describe("TermsOfService", () => {
  it("renders the page heading and sets document.title", () => {
    render(<TermsOfService />);
    screen.getByRole("heading", { name: "Terms of Service", level: 1 });
    expect(document.title).toBe("Terms of Service · AEOMirror");
  });

  it("states scores/recommendations are estimates, never a guaranteed outcome", () => {
    render(<TermsOfService />);
    screen.getByText(/estimates, not\s*guarantees/i);
    screen.getByText(/does not guarantee any specific search\s*ranking/i);
  });

  it("describes the real cancellation behavior (end of billing period, not immediate cutoff)", () => {
    render(<TermsOfService />);
    screen.getByText(/takes effect at the end of your current billing period/i);
  });

  it("does not invent a refund guarantee or a specific governing jurisdiction", () => {
    const { container } = render(<TermsOfService />);
    const text = container.textContent;
    expect(text).not.toMatch(/30-day money-back/i);
    expect(text).not.toMatch(/guarantee(d)? a (full|100%) refund/i);
    // No fabricated country/state name standing in as the governing jurisdiction.
    expect(text).toMatch(/will be confirmed here once finalized/i);
  });
});
