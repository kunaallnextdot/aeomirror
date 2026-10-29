/* Route wiring: /app redirects to /app/dashboard, a detail route reads its id from the
   URL params, and an unknown /app path renders the in-layout 404. AnswerTracking is
   stubbed so the test asserts the wrapper's param wiring, not the heavy view. */
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Outlet } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

vi.mock("../dashboard/AnswerTracking.jsx", () => ({
  default: (props) => <div>site:{String(props.selectedMonitorId)} run:{String(props.selectedRunId)}</div>,
}));

import { AppIndexRedirect, AnswerTrackingRoute, LatestScanRoute, NotFoundRoute } from "./routes.jsx";

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

  it("a detail route reads its id (and nested run id) from the URL params", async () => {
    // The view is lazy-loaded (AppLayout provides the Suspense boundary in the app).
    render(
      <MemoryRouter initialEntries={["/app/answer-tracking/MON1/runs/RUN9"]}>
        <React.Suspense fallback={null}>
          <Routes>
            <Route path="/app/answer-tracking/:monitorId/runs/:runId" element={<AnswerTrackingRoute />} />
          </Routes>
        </React.Suspense>
      </MemoryRouter>,
    );
    expect(await screen.findByText("site:MON1 run:RUN9")).toBeTruthy();
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

describe("Scan Details entry route (/app/scans/latest)", () => {
  // Rendered through a parent route that supplies the layout's Outlet context.
  const renderLatest = (ctx) => {
    return render(
      <MemoryRouter initialEntries={["/app/scans/latest"]}>
        <Routes>
          <Route element={<Outlet context={ctx} />}>
            <Route path="/app/scans/latest" element={<LatestScanRoute />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  };

  it("renders its loading state instead of crashing (was an undefined Aurora component)", () => {
    const { container } = renderLatest({ loading: true, error: null, dashboard: null });
    expect(container.querySelector(".spectrum-screen")).toBeTruthy();
  });

  it("renders its error state with a retry instead of crashing", () => {
    renderLatest({ loading: false, error: "Could not load your dashboard.", dashboard: null, reload: () => {} });
    expect(screen.getByText("Could not load your dashboard.")).toBeTruthy();
  });
});
