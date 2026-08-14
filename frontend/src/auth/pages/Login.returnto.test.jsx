/* After login, the user returns to the destination the auth guard preserved (`?next`),
   falling back to /app — and never to an external (open-redirect) URL. */
import React from "react";
import { render } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const h = vi.hoisted(() => ({ nav: null, authed: true }));
vi.mock("../AuthContext.jsx", () => ({
  useAuth: () => ({ isAuthenticated: h.authed, login: vi.fn() }),
}));
vi.mock("../router.jsx", () => ({ navigate: (...args) => h.nav(...args) }));
vi.mock("../pendingScan.js", () => ({ hasPendingScan: () => false }));

import Login from "./Login.jsx";

describe("Login return-to-intended", () => {
  beforeEach(() => { h.nav = vi.fn(); h.authed = true; });

  it("returns to the ?next destination after login", () => {
    window.history.pushState({}, "", "/login?next=%2Fapp%2Fscans%2Fabc");
    render(<Login />);
    expect(h.nav).toHaveBeenCalledWith("/app/scans/abc", { replace: true });
  });

  it("falls back to /app when there is no next", () => {
    window.history.pushState({}, "", "/login");
    render(<Login />);
    expect(h.nav).toHaveBeenCalledWith("/app", { replace: true });
  });

  it("ignores an external next (no open redirect)", () => {
    window.history.pushState({}, "", "/login?next=%2F%2Fevil.com");
    render(<Login />);
    expect(h.nav).toHaveBeenCalledWith("/app", { replace: true });
  });
});
