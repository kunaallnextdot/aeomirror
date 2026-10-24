/* Every shared Spectrum primitive that renders the AEOMirror brand must link it to the
   public homepage (/), accessibly — covers SpAuthShell (every login/signup/reset/invite
   screen) and TopNav. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import { SpAuthShell, TopNav } from "./spectrum.jsx";

describe("shared/spectrum.jsx — brand logo links home", () => {
  it("SpAuthShell's brand logo links to / with an accessible label", () => {
    render(<MemoryRouter><SpAuthShell title="Log in">content</SpAuthShell></MemoryRouter>);
    const link = screen.getByRole("link", { name: /Go to AEOMirror homepage/ });
    expect(link.getAttribute("href")).toBe("/");
  });

  it("TopNav's brand logo links to / with an accessible label", () => {
    render(<MemoryRouter><TopNav /></MemoryRouter>);
    const link = screen.getByRole("link", { name: /Go to AEOMirror homepage/ });
    expect(link.getAttribute("href")).toBe("/");
  });
});
