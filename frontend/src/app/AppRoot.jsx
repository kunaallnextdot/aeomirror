/* The /app subtree, lazy-loaded as one chunk (keeps the heavy dashboard + charts out of
   the public marketing bundle). Auth-guards the whole subtree, then renders the shared
   layout with the active view supplied by the nested route. */
import React, { useEffect } from "react";
import { Routes, Route } from "react-router-dom";
import { RequireAuth } from "./guards.jsx";
import { clearChunkReloadFlag } from "./RouteErrorBoundary.jsx";
import AppLayout from "./AppLayout.jsx";
import {
  AppIndexRedirect, DashboardHomeRoute, ScansRoute, ScanDetailRoute, LatestScanRoute,
  ActionCenterRoute, MonitoringRoute, MonitorDetailRoute, AnswerTrackingRoute, ReportRoute,
  CompareRoute, SummaryRoute, BillingRoute, ProfileRoute, TeamRoute, OrganizationRoute,
  NotFoundRoute,
} from "./routes.jsx";

export default function AppRoot() {
  // Reaching here means the authenticated chunk loaded — clear the one-shot reload guard
  // so a FUTURE (unrelated) chunk failure can auto-reload once again. A still-broken chunk
  // never mounts this, so its flag stays set and it can't loop.
  useEffect(() => { clearChunkReloadFlag(); }, []);
  return (
    <RequireAuth>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<AppIndexRedirect />} />
          <Route path="dashboard" element={<DashboardHomeRoute />} />
          <Route path="scans" element={<ScansRoute />} />
          <Route path="scans/latest" element={<LatestScanRoute />} />
          <Route path="scans/:scanId" element={<ScanDetailRoute />} />
          <Route path="action-center" element={<ActionCenterRoute />} />
          <Route path="monitoring" element={<MonitoringRoute />} />
          <Route path="monitoring/:monitorId" element={<MonitorDetailRoute />} />
          <Route path="answer-tracking" element={<AnswerTrackingRoute />} />
          <Route path="answer-tracking/:monitorId" element={<AnswerTrackingRoute />} />
          <Route path="answer-tracking/:monitorId/runs/:runId" element={<AnswerTrackingRoute />} />
          <Route path="report" element={<ReportRoute />} />
          <Route path="compare" element={<CompareRoute />} />
          <Route path="website-summary" element={<SummaryRoute />} />
          <Route path="billing" element={<BillingRoute />} />
          <Route path="profile" element={<ProfileRoute />} />
          <Route path="team" element={<TeamRoute />} />
          <Route path="organization" element={<OrganizationRoute />} />
          <Route path="*" element={<NotFoundRoute />} />
        </Route>
      </Routes>
    </RequireAuth>
  );
}
