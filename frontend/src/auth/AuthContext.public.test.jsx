/* The public shared-report route (/r/:token) must fire NO auth request: a signed-out
   visitor should not trigger a refresh cycle, a signed-in one should not attach a
   session. AuthProvider guards its bootstrap on the /r/ path. */
import React from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { AuthProvider } from "./AuthContext.jsx";

function setPath(p) { window.history.pushState({}, "", p); }

describe("AuthProvider bootstrap gating on /r/", () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
  });

  it("fires NO auth request for a signed-out visitor on /r/:token", async () => {
    setPath("/r/some-share-token");
    render(<AuthProvider><div>public</div></AuthProvider>);
    await new Promise((r) => setTimeout(r, 25));   // let any (guarded-away) effect run
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("DOES bootstrap on a normal route (control)", async () => {
    setPath("/app");
    render(<AuthProvider><div>app</div></AuthProvider>);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());   // refreshSession → /auth/refresh
  });
});
