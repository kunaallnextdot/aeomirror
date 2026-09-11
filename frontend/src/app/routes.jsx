/* Route elements for the authenticated app. Each wrapper reads its selection from the
   URL (params/query) and pulls shared data + actions from the layout's Outlet context,
   then renders the existing view component unchanged. The URL is the single source of
   truth for every selection. */
import React, { useCallback, useEffect, useState } from "react";
import {
  Link, Navigate, useNavigate, useOutletContext, useParams, useSearchParams,
} from "react-router-dom";
import { getScanDetail, compareScans, ScanError } from "../api.js";
import { fmtDate } from "../dashboard/ui.jsx";
import DashboardHome from "../dashboard/DashboardHome.jsx";
import ScansTable, { ScansLoading, ScansEmpty, ScansError } from "../dashboard/ScansTable.jsx";
import { AuroraSkeletonPage, AuroraEmptyScans, AuroraError, Shell, Cell, Ring } from "../dashboard/aurora.jsx";
const auScoreColor = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");
import ScanDetails, { ScanDetailLoading, ScanDetailNotFound } from "../dashboard/ScanDetails.jsx";
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

/* Scan-list gate for the migrated routes (home/scans/compare/summary/report): loading ->
   skeleton, error -> error, no scans -> empty state. (Replaced the former dark <Gated>.) */
function AuroraGated({ children }) {
  const { loading, error, scans, reload, onRunScan } = useAppCtx();
  if (error) return <AuroraError message={error} onRetry={reload} />;
  if (loading) return <AuroraSkeletonPage />;
  if (!scans.length) return <AuroraEmptyScans onRun={onRunScan} />;
  return children;
}

export function DashboardHomeRoute() {
  const { dashboard, openDetail } = useAppCtx();
  return <AuroraGated><DashboardHome data={dashboard} onOpenLatest={openDetail} /></AuroraGated>;
}

export function ScansRoute() {
  const { scans, busyId, openDetail, onRerun, onDelete, canRun, canDelete, openCompare,
          loading, error, reload, onRunScan } = useAppCtx();
  // Aurora-scoped guard for THIS route only — same conditions as the shared <Gated>, but
  // re-skinned so migrating /app/scans doesn't touch Gated/EmptyState/ErrorState (still used
  // by Dashboard/Compare/Summary/Report).
  if (error) return <ScansError message={error} onRetry={reload} />;
  if (loading) return <ScansLoading />;
  if (!scans.length) return <ScansEmpty onRun={onRunScan} />;
  return (
    <ScansTable scans={scans} busyId={busyId} onView={openDetail} onRerun={onRerun}
                onDelete={onDelete} canRun={canRun} canDelete={canDelete}
                onCompareSelected={openCompare} />
  );
}

export function CompareRoute() {
  const { scans } = useAppCtx();
  return <AuroraGated><Compare scans={scans} onCompare={compareScans} /></AuroraGated>;
}

export function ReportRoute() {
  const { dashboard } = useAppCtx();
  const [params] = useSearchParams();
  const scanId = params.get("scan") || dashboard?.latest_scan?.id;
  return <AuroraGated><ReportView scanId={scanId} /></AuroraGated>;
}

export function SummaryRoute() {
  const { scans, openDetail } = useAppCtx();
  return <AuroraGated><WebsiteSummary scans={scans} onView={openDetail} /></AuroraGated>;
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

  // Aurora-skinned states for THIS route only (shared NotFoundState/TableSkeleton untouched).
  if (error) return <ScanDetailNotFound label={error} />;   // includes cross-org 404 ("not found")
  if (!scan) return <ScanDetailLoading />;
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
    <div className="aurora-screen"><Shell>
      <Cell solid><div className="au-card-center">
        <div className="au-card-t">Page not found</div>
        <div className="au-card-s">This page doesn’t exist under your workspace.</div>
        <Link className="au-btn au-accent" to="/app/dashboard">Back to dashboard</Link>
      </div></Cell>
    </Shell></div>
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
    return (
      <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center">
        <div className="au-ill"><Globe size={26} /></div>
        <div className="au-card-s">No websites yet — run a scan to see per-domain rollups here.</div>
      </div></Cell></Shell></div>
    );
  }
  return (
    <div className="aurora-screen"><Shell>
      <div className="au-stack">
        {rows.map((g) => (
          <Cell key={g.domain} solid style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
            <span role="img" aria-label={`Score ${g.latest.overall_score ?? "not available"}`}><Ring value={g.latest.overall_score} size={52} stroke={6} /></span>
            <div style={{ flex: 1, minWidth: 160 }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: "var(--au-ink)" }}>{g.domain}</div>
              <div className="au-dim au-mono" style={{ fontSize: 11.5, marginTop: 3 }}>{g.count} scan{g.count === 1 ? "" : "s"} · latest {fmtDate(g.latest.scan_time)}</div>
            </div>
            <div style={{ display: "flex", gap: 18, textAlign: "center" }}>
              <div><div style={{ color: auScoreColor(g.best), fontWeight: 700, fontFamily: "var(--au-font-numeric)" }}>{g.best ?? "-"}</div><div className="au-dim" style={{ fontSize: 11 }}>best</div></div>
              <div><div style={{ color: auScoreColor(g.worst), fontWeight: 700, fontFamily: "var(--au-font-numeric)" }}>{g.worst ?? "-"}</div><div className="au-dim" style={{ fontSize: 11 }}>worst</div></div>
            </div>
            <button className="au-iconbtn" onClick={() => onView(g.latest.id)}>View latest</button>
          </Cell>
        ))}
      </div>
    </Shell></div>
  );
}
