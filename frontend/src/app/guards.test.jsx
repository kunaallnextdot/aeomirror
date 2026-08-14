/* Auth guard: loading while the session check is unresolved (never redirect), redirect
   to login preserving the intended destination when definitively unauthenticated, and
   render the protected content when authenticated. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

const h = vi.hoisted(() => ({ auth: { ready: false, isAuthenticated: false, user: null } }));
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => h.auth }));

import { RequireAuth } from "./guards.jsx";

function LoginProbe() {
  const loc = useLocation();
  return <div>LOGIN{loc.search}</div>;
}

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginProbe />} />
        <Route path="/app/*" element={<RequireAuth><div>SECRET</div></RequireAuth>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  it("renders loading while the auth check is unresolved (does NOT redirect)", () => {
    h.auth = { ready: false, isAuthenticated: false, user: null };
    renderAt("/app/scans");
    expect(screen.getByText(/Loading/)).toBeTruthy();
    expect(screen.queryByText("SECRET")).toBeNull();
    expect(screen.queryByText(/LOGIN/)).toBeNull();     // no redirect during unresolved check
  });

  it("redirects an unauthenticated user to login, preserving the intended destination", () => {
    h.auth = { ready: true, isAuthenticated: false, user: null };
    renderAt("/app/scans/abc123");
    const el = screen.getByText(/LOGIN/);
    expect(el.textContent).toContain("next=");
    expect(el.textContent).toContain(encodeURIComponent("/app/scans/abc123"));
    expect(screen.queryByText("SECRET")).toBeNull();
  });

  it("renders the protected content when authenticated", () => {
    h.auth = { ready: true, isAuthenticated: true, user: { id: "u1" } };
    renderAt("/app/scans");
    expect(screen.getByText("SECRET")).toBeTruthy();
  });
});
