/* Route wiring: /app redirects to /app/dashboard, a detail route reads its id from the
   URL params, and an unknown /app path renders the in-layout 404. AnswerTracking is
   stubbed so the test asserts the wrapper's param wiring, not the heavy view. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../dashboard/AnswerTracking.jsx", () => ({
  default: (props) => <div>site:{String(props.selectedMonitorId)} run:{String(props.selectedRunId)}</div>,
}));

import { AppIndexRedirect, AnswerTrackingRoute, NotFoundRoute } from "./routes.jsx";

describe("app routing", () => {
  it("redirects /app to /app/dashboard", () => {
    render(
      <MemoryRouter initialEntries={["/app"]}>
        <Routes>
          <Route path="/app">
            <Route index element={<AppIndexRedirect />} />
            <Route path="dashboard" element={<div>DASHBOARD</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("DASHBOARD")).toBeTruthy();
  });

  it("a detail route reads its id (and nested run id) from the URL params", () => {
    render(
      <MemoryRouter initialEntries={["/app/answer-tracking/MON1/runs/RUN9"]}>
        <Routes>
          <Route path="/app/answer-tracking/:monitorId/runs/:runId" element={<AnswerTrackingRoute />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("site:MON1 run:RUN9")).toBeTruthy();
  });

  it("an unknown /app route renders the in-layout 404", () => {
    render(
      <MemoryRouter initialEntries={["/app/does-not-exist"]}>
        <Routes>
          <Route path="/app/dashboard" element={<div>DASHBOARD</div>} />
          <Route path="*" element={<NotFoundRoute />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(/Page not found/)).toBeTruthy();
  });
});
