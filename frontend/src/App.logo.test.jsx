/* Every AEOMirror logo instance must be a real, accessible link to the public
   homepage (/) — never a div with an onClick-only handler. Covers the public
   marketing header (TopBar) and site footer (SiteFooter) from App.jsx; the
   authenticated sidebar/footer brand (AppLayout.jsx) and the auth-page brand
   (SpAuthShell, shared/spectrum.jsx) are covered in their own test files. */
import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("./auth/AuthContext.jsx", () => ({
  useAuth: () => ({ ready: true, isAuthenticated: false, user: null }),
}));

import { TopBar, SiteFooter } from "./App.jsx";

describe("App.jsx — every logo instance links home", () => {
  it("TopBar's brand logo links to / with an accessible label", () => {
    render(<MemoryRouter><TopBar /></MemoryRouter>);
    const link = screen.getByRole("link", { name: /Go to AEOMirror homepage/ });
    expect(link.getAttribute("href")).toBe("/");
  });

  it("SiteFooter's brand logo links to / with an accessible label, separately from the tagline text", () => {
    render(<MemoryRouter><SiteFooter /></MemoryRouter>);
    const link = screen.getByRole("link", { name: /Go to AEOMirror homepage/ });
    expect(link.getAttribute("href")).toBe("/");
    // the tagline is real visible text, not swallowed into the link's accessible name
    expect(screen.getByText(/See what AI can reach, read and cite/)).toBeTruthy();
  });

  it("SiteFooter's logo link is a real <a>, not a button nested where it could trigger something else", () => {
    render(<MemoryRouter><SiteFooter /></MemoryRouter>);
    const link = screen.getByRole("link", { name: /Go to AEOMirror homepage/ });
    expect(link.tagName).toBe("A");
    expect(within(link).queryByRole("button")).toBeNull();
  });
});
