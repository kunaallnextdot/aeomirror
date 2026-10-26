/* End-to-end session restore: AuthProvider's silent refresh-on-mount + RequireAuth
   together, exactly as they run in the real app shell. Covers the actual regression
   report — a valid session must survive a hard page refresh, on the SAME path and
   query string, with no redirect to /login while restoration is still in flight. */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { AuthProvider } from "./AuthContext.jsx";
import { RequireAuth } from "../app/guards.jsx";

function setPath(p) { window.history.pushState({}, "", p); }

function LoginProbe() {
  const loc = useLocation();
  return <div>LOGIN{loc.search}</div>;
}

function renderApp() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
        <Routes>
          <Route path="/login" element={<LoginProbe />} />
          <Route path="/app/*" element={<RequireAuth><div>SECRET PAGE</div></RequireAuth>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("session restore on page refresh (AuthProvider + RequireAuth)", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("a valid session survives a refresh: stays on the same path+query, never flashes to /login", async () => {
    setPath("/app/business-impact?site_id=123");
    global.fetch = vi.fn((url) => {
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve({ ok: true, json: async () => ({ access_token: "tok1" }) });
      }
      if (String(url).endsWith("/me")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: async () => ({ user: { id: "u1", role: "owner" }, organization: { id: "o1" } }),
        });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    });

    renderApp();
    // While restoration is in flight, neither the protected content nor a login
    // redirect has appeared yet.
    expect(screen.queryByText("SECRET PAGE")).toBeNull();
    expect(screen.queryByText(/LOGIN/)).toBeNull();

    await waitFor(() => screen.getByText("SECRET PAGE"));
    expect(screen.queryByText(/LOGIN/)).toBeNull();   // never redirected
  });

  it("a genuinely invalid session redirects to login, preserving the exact path and query string", async () => {
    setPath("/app/business-impact?site_id=123");
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    renderApp();
    const el = await waitFor(() => screen.getByText(/LOGIN/));
    expect(el.textContent).toContain(encodeURIComponent("/app/business-impact?site_id=123"));
    expect(screen.queryByText("SECRET PAGE")).toBeNull();
  });

  it("a failed refresh never loops — the refresh endpoint is called exactly once, not repeatedly", async () => {
    setPath("/app/dashboard");
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    renderApp();
    await waitFor(() => screen.getByText(/LOGIN/));
    const refreshCalls = global.fetch.mock.calls.filter(([url]) => String(url).includes("/auth/refresh"));
    expect(refreshCalls.length).toBe(1);
  });
});
