/* Content Insights ("Analyze content") timeout chain — regression coverage for the
   production bug where the frontend's generic 60s request ceiling aborted a
   legitimately-completing (but slower, up to ~75s backend budget) analysis before it
   finished, surfacing a client-side "timed out" error even though the backend would
   have returned a real result. Fixed by giving analyzeContent() its own, larger,
   feature-specific timeout (CONTENT_INSIGHT_TIMEOUT_MS) via request()'s new
   `timeoutMs` option, WITHOUT changing the generic REQUEST_TIMEOUT_MS every other
   call still uses — these tests pin both halves of that contract.

   authFetch (the actual `fetch()` call) is mocked at the module boundary so these
   tests exercise the REAL request()/messageForStatus() logic in api.js, not a stub. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("./auth/client.js", () => ({ authFetch: vi.fn() }));

import { authFetch } from "./auth/client.js";
import { analyzeContent, getDashboard } from "./api.js";

// Mimics real fetch()+AbortController semantics: resolves after `ms`, or rejects with
// a genuine AbortError the instant the request() timer's signal fires — exactly what
// a real aborted fetch does.
function delayedResponse(ms, body, status = 200) {
  return (_path, opts) => new Promise((resolve, reject) => {
    const t = setTimeout(() => resolve({
      ok: status < 400, status, json: async () => body,
    }), ms);
    opts?.signal?.addEventListener("abort", () => {
      clearTimeout(t);
      const err = new Error("The operation was aborted.");
      err.name = "AbortError";
      reject(err);
    });
  });
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

describe("Content Insights timeout chain", () => {
  it("1 & 2: a successful, valid response resolves with the real parsed JSON", async () => {
    const body = { scan_id: "s1", page_url: "https://x.com", cached: false, insights: { tone: { score_0_100: 80 } } };
    authFetch.mockImplementation(delayedResponse(500, body));
    const promise = analyzeContent("s1");
    await vi.advanceTimersByTimeAsync(500);
    await expect(promise).resolves.toEqual(body);
  });

  it("4: analyzeContent does NOT prematurely abort a slow-but-legitimate analysis (70s < its 90s ceiling, even though that's past the generic 60s ceiling)", async () => {
    const body = { scan_id: "s1", page_url: "https://x.com", cached: false, insights: { tone: { score_0_100: 55 } } };
    authFetch.mockImplementation(delayedResponse(70000, body));
    const promise = analyzeContent("s1");
    await vi.advanceTimersByTimeAsync(70000);
    await expect(promise).resolves.toEqual(body);
  });

  it("3: a genuinely too-slow analyze call (past its own 90s ceiling) still times out honestly", async () => {
    authFetch.mockImplementation(delayedResponse(95000, {}));
    const promise = analyzeContent("s1");
    const assertion = expect(promise).rejects.toMatchObject({ name: "ScanError", code: "timeout" });
    await vi.advanceTimersByTimeAsync(95000);
    await assertion;
  });

  it("3 (control): an ordinary call still uses the generic 60s ceiling, unaffected by analyzeContent's override — a 70s stall on a generic endpoint is a timeout", async () => {
    authFetch.mockImplementation(delayedResponse(70000, {}));
    const promise = getDashboard();
    const assertion = expect(promise).rejects.toMatchObject({ name: "ScanError", code: "timeout" });
    await vi.advanceTimersByTimeAsync(70000);
    await assertion;
  });

  it("5: existing error behavior is unaffected — a real curated 503 detail still surfaces verbatim", async () => {
    authFetch.mockImplementation(delayedResponse(10, { detail: "AI analysis is temporarily unavailable." }, 503));
    const promise = analyzeContent("s1");
    const assertion = expect(promise).rejects.toMatchObject({
      name: "ScanError", code: 503, message: "AI analysis is temporarily unavailable.",
    });
    await vi.advanceTimersByTimeAsync(10);
    await assertion;
  });
});
