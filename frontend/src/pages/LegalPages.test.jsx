/* Privacy Policy (/privacy) + Terms of Service (/terms) — pure rendering tests, same
   isolation convention as PublicReport.test.jsx (render the leaf component directly;
   neither page calls an API or useAuth(), so no mocking is needed — this also doubles
   as proof these pages have no authentication dependency, since the full App.jsx
   route table keeps /privacy and /terms outside the RequireAuth-gated /app/* tree). */
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
    expect(screen.getAllByText(/read-only/i).length).toBeGreaterThan(0);
    screen.getByText(/is encrypted before it is stored in our database/i);
    screen.getByText(/we never store your google access token/i);
  });

  it("names exactly one Google OAuth scope and explicitly rules out Gmail, Drive, Contacts, and Calendar", () => {
    render(<PrivacyPolicy />);
    screen.getByText("https://www.googleapis.com/auth/analytics.readonly");
    screen.getByText(/do not request access to gmail, google drive, google contacts, google calendar/i);
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

  it("has a standalone 'Data sharing, transfers, and disclosures' section naming which provider can access Google user data (Render) and which cannot (AI providers, Stripe, Resend, Vercel)", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("heading", { name: /Data sharing, transfers, and disclosures/i });
    screen.getByText(/not.*sent to, or made accessible to, any\s*of our other service providers/i);
    screen.getByText(/not to our ai providers \(anthropic, openai,\s*or perplexity\)/i);
    screen.getByText(/not to our payment processor \(stripe\)/i);
    screen.getByText(/not to our email\s*provider \(resend\)/i);
    screen.getByText(/not to vercel, which hosts only our static frontend/i);
  });

  it("includes the Google API Services User Data Policy / Limited Use disclosure, and explicitly rules out selling data or using it for ads", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("link", { name: /Google API Services User Data Policy/i });
    screen.getByText(/including its Limited Use requirements/i);
    screen.getByText(/we do not sell google user data/i);
    screen.getByText(/to serve\s*advertisements of any kind/i);
  });

  it("has a standalone 'AI and machine learning restrictions' section with the required no-training / no-advertising statement, and separates it from the unrelated content-AI features", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("heading", { name: /AI and machine learning restrictions/i });
    screen.getByText(/does not use the google analytics api, or any other google api, to\s*develop, improve, or train/i);
    screen.getByText(/not used\s*for targeted advertising, personalized advertising, or unrelated profiling/i);
    screen.getByText(/never receive your google analytics data, your google refresh token, or any other\s*google-sourced information/i);
  });

  it("explains disconnect/revoke behavior: Google-side revoke, immediate token deletion, and that historical data is kept unless deletion is requested", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("heading", { name: /Disconnecting a Google account and revoking access/i });
    screen.getByText(/we attempt to revoke the credential with google\s*directly/i);
    screen.getByRole("link", { name: /third-party access settings/i });
    screen.getByText(/historical traffic numbers already synced before you disconnected are kept/i);
  });

  it("has a Children's privacy section", () => {
    render(<PrivacyPolicy />);
    screen.getByRole("heading", { name: /Children's privacy/i });
    screen.getByText(/not knowingly collect personal information from children/i);
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
