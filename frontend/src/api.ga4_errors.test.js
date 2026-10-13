/* Regression test for a real production bug: AI Traffic page showed "The scanner hit
   an unexpected error" for a Google Analytics failure. Root cause was a stale local
   dev DB missing the GA4 migration (fixed by running `alembic upgrade head`), but the
   frontend copy bug was real and independent of that: GA4 endpoints didn't pass a
   feature-specific `genericMessage`, so ANY unhandled 5xx from them (for any reason,
   not just this one) fell through to request()'s hardcoded scanner-wording default.
   This pins that the GA4 client functions now show GA4-appropriate copy instead. */
import { describe, it, expect, vi } from "vitest";

vi.mock("./auth/client.js", () => ({ authFetch: vi.fn() }));

import { authFetch } from "./auth/client.js";
import { getGa4Status, getGa4Traffic, connectGoogleAnalytics, getDashboard } from "./api.js";

function jsonResponse(body, status) {
  return async () => ({ ok: status < 400, status, json: async () => body });
}

describe("GA4 error messaging — an unhandled 5xx must not show scanner-specific copy", () => {
  it("a bare 500 (no detail body) from GA4 /status shows GA4-specific wording, not the scanner default", async () => {
    authFetch.mockImplementation(jsonResponse({}, 500));
    await expect(getGa4Status()).rejects.toMatchObject({
      name: "ScanError",
      message: expect.stringContaining("Google Analytics"),
    });
  });

  it("the scanner's generic wording never leaks into a GA4 error message", async () => {
    authFetch.mockImplementation(jsonResponse({}, 503));
    try {
      await getGa4Traffic();
      throw new Error("expected getGa4Traffic to reject");
    } catch (e) {
      expect(e.message).not.toMatch(/scanner hit an unexpected error/i);
      expect(e.message).toMatch(/google analytics/i);
    }
  });

  it("control: an ordinary (non-GA4) endpoint still uses the original scanner wording on a bare 5xx", async () => {
    authFetch.mockImplementation(jsonResponse({}, 500));
    await expect(getDashboard()).rejects.toMatchObject({
      name: "ScanError",
      message: expect.stringContaining("scanner hit an unexpected error"),
    });
  });

  it("a real curated detail (e.g. 402 Pro-required) still wins over the generic GA4 fallback", async () => {
    authFetch.mockImplementation(jsonResponse({ detail: "Google Analytics traffic is a Pro feature." }, 402));
    await expect(getGa4Status()).rejects.toMatchObject({
      code: 402, message: "Google Analytics traffic is a Pro feature.",
    });
  });

  it("missing Google OAuth env vars: connect()'s deliberate 503 'not configured' detail is shown verbatim, not the generic GA4 fallback", async () => {
    // This is the exact 5xx routes_integrations.py's _require_enabled() raises when
    // GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI are unset. Without errorContext:true
    // on connectGoogleAnalytics(), messageForStatus() would discard this real detail
    // (503 is >=500) and show the vague GA4_GENERIC_MESSAGE instead — which fails the
    // product requirement that an unconfigured integration shows a CLEAR
    // configuration state, not a vague "try again" message.
    authFetch.mockImplementation(jsonResponse(
      { detail: "Google Analytics integration is not configured on this server." }, 503));
    await expect(connectGoogleAnalytics()).rejects.toMatchObject({
      code: 503, message: "Google Analytics integration is not configured on this server.",
    });
  });
});
