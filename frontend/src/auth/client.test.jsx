/* auth/client.js — the authenticated fetch wrapper. Covers the token-refresh retry
   contract directly: exactly one refresh per 401, exactly one retry (never a loop
   even if the retry also 401s), and concurrent 401s share a single in-flight
   refresh (no refresh "stampede" / duplicate refresh requests). */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { authFetch, setAccessToken, onSessionCleared } from "./client.js";

function jsonOk(body) {
  return { ok: true, status: 200, json: async () => body };
}
function unauthorized() {
  return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
}

describe("authFetch — refresh-on-401 contract", () => {
  beforeEach(() => {
    setAccessToken(null);
    onSessionCleared(() => {});
  });

  it("on a 401, refreshes exactly once and retries the original request exactly once", async () => {
    let call = 0;
    global.fetch = vi.fn((url) => {
      call += 1;
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve(jsonOk({ access_token: "new-token" }));
      }
      // First call to the real endpoint -> 401; the retry (2nd call) -> success.
      return Promise.resolve(call <= 1 ? unauthorized() : jsonOk({ ok: true }));
    });

    const res = await authFetch("/business-impact/summary");
    expect(res.ok).toBe(true);
    const refreshCalls = global.fetch.mock.calls.filter(([u]) => String(u).includes("/auth/refresh"));
    expect(refreshCalls.length).toBe(1);
    // original request + refresh + retried request = 3 fetch calls, never more.
    expect(global.fetch.mock.calls.length).toBe(3);
  });

  it("never loops when the retried request ALSO returns 401", async () => {
    global.fetch = vi.fn((url) => {
      if (String(url).includes("/auth/refresh")) return Promise.resolve(jsonOk({ access_token: "new-token" }));
      return Promise.resolve(unauthorized());   // every real-endpoint call 401s
    });

    const res = await authFetch("/business-impact/summary");
    expect(res.status).toBe(401);   // surfaces the 401, does not retry again
    const refreshCalls = global.fetch.mock.calls.filter(([u]) => String(u).includes("/auth/refresh"));
    expect(refreshCalls.length).toBe(1);
    expect(global.fetch.mock.calls.length).toBe(3);   // original + refresh + one retry, no more
  });

  it("clears the session (never loops into another refresh) when the refresh itself fails", async () => {
    const cleared = vi.fn();
    onSessionCleared(cleared);
    global.fetch = vi.fn((url) => {
      if (String(url).includes("/auth/refresh")) return Promise.resolve(unauthorized());
      return Promise.resolve(unauthorized());
    });

    const res = await authFetch("/business-impact/summary");
    expect(res.status).toBe(401);
    expect(cleared).toHaveBeenCalledTimes(1);
    const refreshCalls = global.fetch.mock.calls.filter(([u]) => String(u).includes("/auth/refresh"));
    expect(refreshCalls.length).toBe(1);   // exactly one attempt, never retried into a loop
  });

  it("concurrent 401s share a single in-flight refresh — no duplicate refresh requests", async () => {
    let refreshCount = 0;
    global.fetch = vi.fn((url) => {
      if (String(url).includes("/auth/refresh")) {
        refreshCount += 1;
        return new Promise((resolve) => setTimeout(() => resolve(jsonOk({ access_token: "t" })), 10));
      }
      return Promise.resolve(jsonOk({ ok: true }));   // every real call succeeds post-refresh
    });
    // Two independent 401-triggering calls racing at the same time.
    global.fetch.mockImplementationOnce((url) => Promise.resolve(unauthorized()))
               .mockImplementationOnce((url) => Promise.resolve(unauthorized()));

    await Promise.all([authFetch("/a"), authFetch("/b")]);
    expect(refreshCount).toBe(1);
  });
});
