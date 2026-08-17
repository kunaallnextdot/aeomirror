/* Route elements for the authenticated app. Each wrapper reads its selection from the
   URL (params/query) and pulls shared data + actions from the layout's Outlet context,
   then renders the existing view component unchanged. The URL is the single source of
   truth for every selection. */
import React, { useCallback, useEffect, useState } from "react";
import {
  Link, Navigate, useNavigate, useOutletContext, useParams, useSearchParams,
} from "react-router-dom";
import { getScanDetail, compareScans, ScanError } from "../api.js";
import {
  StatsSkeleton, TableSkeleton, EmptyState, ErrorState, MiniEmpty, ScoreRing, scoreColor, fmtDate,
} from "../dashboard/ui.jsx";
import DashboardHome from "../dashboard/DashboardHome.jsx";
import ScansTable from "../dashboard/ScansTable.jsx";
import ScanDetails from "../dashboard/ScanDetails.jsx";
import Compare from "../dashboard/Compare.jsx";
import ReportView from "../dashboard/ReportView.jsx";
import Monitoring from "../dashboard/Monitoring.jsx";
import MonitorDetail from "../dashboard/MonitorDetail.jsx";
import AnswerTracking from "../dashboard/AnswerTracking.jsx";
import BillingView from "../dashboard/BillingView.jsx";
import ProfilePage from "../auth/pages/ProfilePage.jsx";
import TeamPage from "../auth/pages/TeamPage.jsx";
import OrganizationPage from "../auth/pages/OrganizationPage.jsx";
import { Globe } from "lucide-react";

export function useAppCtx() { return useOutletContext(); }

/* Gate for the views that depend on the shared scan list (home/scans/compare/summary/
   report): loading -> skeleton, error -> error, no scans -> empty state. */
function Gated({ children }) {
  const { loading, error, scans, reload, onRunScan } = useAppCtx();
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (loading) return <><StatsSkeleton /><TableSkeleton /></>;
  if (!scans.length) return <EmptyState onRun={onRunScan} />;
  return children;
}

export function DashboardHomeRoute() {
  const { dashboard, openDetail } = useAppCtx();
  return <Gated><DashboardHome data={dashboard} onOpenLatest={openDetail} /></Gated>;
}

export function ScansRoute() {
  const { scans, busyId, openDetail, onRerun, onDelete, canRun, canDelete, openCompare } = useAppCtx();
  return (
    <Gated>
      <ScansTable scans={scans} busyId={busyId} onView={openDetail} onRerun={onRerun}
                  onDelete={onDelete} canRun={canRun} canDelete={canDelete}
                  onCompareSelected={openCompare} />
    </Gated>
  );
}

export function CompareRoute() {
  const { scans } = useAppCtx();
  return <Gated><Compare scans={scans} onCompare={compareScans} /></Gated>;
}

export function ReportRoute() {
  const { dashboard } = useAppCtx();
  const [params] = useSearchParams();
  const scanId = params.get("scan") || dashboard?.latest_scan?.id;
  return <Gated><ReportView scanId={scanId} /></Gated>;
}

export function SummaryRoute() {
  const { scans, openDetail } = useAppCtx();
  return <Gated><WebsiteSummary scans={scans} onView={openDetail} /></Gated>;
}

/* Scan detail loads its own record by :scanId (not gated by the shared list). */
export function ScanDetailRoute() {
  const { scanId } = useParams();
  const navigate = useNavigate();
  const { onRerun, busyId, canRun, openReport, retryBulkScan, reloadSubscription } = useAppCtx();
  const [scan, setScan] = useState(null);
  const [error, setError] = useState(null);

  const loadDetail = useCallback(async () => {
    setError(null); setScan(null);
    try { setScan(await getScanDetail(scanId)); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not load that scan."); }
  }, [scanId]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  const refresh = useCallback(async () => {
    try { setScan(await getScanDetail(scanId)); reloadSubscription?.(); }
    catch { /* keep view; poller retries */ }
  }, [scanId, reloadSubscription]);

  if (error) return <NotFoundState label={error} />;   // includes cross-org 404 ("not found")
  if (!scan) return <TableSkeleton rows={4} />;
  return (
    <ScanDetails scan={scan} onBack={() => navigate("/app/scans")} onRerun={onRerun}
                 busy={busyId === scan.scan_id} canRun={canRun}
                 onReport={() => openReport(scan.scan_id)}
                 onRefresh={refresh} onRetryBulk={retryBulkScan} />
  );
}

export function MonitoringRoute() {
  const { openMonitor } = useAppCtx();
  return <Monitoring onOpenMonitor={openMonitor} />;
}

export function MonitorDetailRoute() {
  const { monitorId } = useParams();
  const navigate = useNavigate();
  const { openReport } = useAppCtx();
  return <MonitorDetail key={monitorId} monitorId={monitorId}
                        onBack={() => navigate("/app/monitoring")} onOpenReport={openReport} />;
}

export function AnswerTrackingRoute() {
  const { monitorId, runId } = useParams();
  return <AnswerTracking selectedMonitorId={monitorId || null} selectedRunId={runId || null} />;
}

export function BillingRoute() { return <BillingView />; }
export function ProfileRoute() { return <ProfilePage />; }
export function TeamRoute() { return <TeamPage />; }
export function OrganizationRoute() { return <OrganizationPage />; }
export function AppIndexRedirect() { return <Navigate to="/app/dashboard" replace />; }

export function NotFoundRoute() {
  return (
    <div className="d-panel" style={{ textAlign: "center", padding: "40px 20px" }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>Page not found</div>
      <div className="d-dim" style={{ fontSize: 13, marginBottom: 14 }}>
        This page doesn’t exist under your workspace.
      </div>
      <Link className="d-btn" to="/app/dashboard">Back to dashboard</Link>
    </div>
  );
}

/* Shown when a valid route points at an id that doesn't exist or belongs to another org
   (the backend returns 404, never 403, so ids can't be probed). Not a crash, not a
   spinner. */
export function NotFoundState({ label }) {
  return (
    <div className="d-panel" style={{ textAlign: "center", padding: "36px 20px" }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>Not found or no access</div>
      <div className="d-dim" style={{ fontSize: 13, marginBottom: 14 }}>
        {label || "This item doesn’t exist, or it belongs to another workspace."}
      </div>
      <Link className="d-btn" to="/app/dashboard">Back to dashboard</Link>
    </div>
  );
}

/* Website summary (per-domain rollup) — moved verbatim from the old Dashboard shell. */
function WebsiteSummary({ scans, onView }) {
  const byDomain = {};
  for (const s of scans) {
    const g = byDomain[s.domain] || (byDomain[s.domain] = { domain: s.domain, scans: [], scores: [] });
    g.scans.push(s);
    if (s.overall_score != null) g.scores.push(s.overall_score);
  }
  const rows = Object.values(byDomain).map((g) => {
    const latest = g.scans.slice().sort((a, b) => new Date(b.scan_time) - new Date(a.scan_time))[0];
    return { ...g, latest, count: g.scans.length,
             best: g.scores.length ? Math.max(...g.scores) : null,
             worst: g.scores.length ? Math.min(...g.scores) : null };
  }).sort((a, b) => new Date(b.latest.scan_time) - new Date(a.latest.scan_time));

  if (rows.length === 0) {
    return <MiniEmpty icon={Globe} line="No websites yet — run a scan to see per-domain rollups here." />;
  }
  return (
    <div className="d-grid" style={{ gap: 14 }}>
      {rows.map((g) => (
        <div key={g.domain} className="d-card" style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
          <ScoreRing value={g.latest.overall_score} size={52} stroke={6} />
          <div style={{ flex: 1, minWidth: 160 }}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{g.domain}</div>
            <div className="d-dim d-mono" style={{ fontSize: 11.5, marginTop: 3 }}>{g.count} scan{g.count === 1 ? "" : "s"} · latest {fmtDate(g.latest.scan_time)}</div>
          </div>
          <div style={{ display: "flex", gap: 18, textAlign: "center" }}>
            <div><div style={{ color: scoreColor(g.best), fontWeight: 700, fontFamily: "'IBM Plex Mono'" }}>{g.best ?? "-"}</div><div className="d-dim" style={{ fontSize: 11 }}>best</div></div>
            <div><div style={{ color: scoreColor(g.worst), fontWeight: 700, fontFamily: "'IBM Plex Mono'" }}>{g.worst ?? "-"}</div><div className="d-dim" style={{ fontSize: 11 }}>worst</div></div>
          </div>
          <button className="d-iconbtn" onClick={() => onView(g.latest.id)}>View latest</button>
        </div>
      ))}
    </div>
  );
}
