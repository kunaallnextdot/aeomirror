/* Product dashboard shell (Phase 4): sidebar nav, view routing, data fetching,
   and the loading / empty / error states. Wires the API layer to the views. */
import React, { useCallback, useEffect, useState } from "react";
import {
  Radar, LayoutDashboard, ScanLine, GitCompare, Globe, ArrowLeft, FileText,
  Activity, User, Users, Building2, LogOut, Shield, CreditCard, MessageSquare,
} from "lucide-react";
import { getDashboard, getScans, getScanDetail, compareScans, deleteScan, rerunScan, bulkScanUrls, ScanError } from "../api.js";
import { StatsSkeleton, TableSkeleton, EmptyState, ErrorState, MiniEmpty, ScoreRing, scoreColor, fmtDate } from "./ui.jsx";
import DashboardHome from "./DashboardHome.jsx";
import ScansTable from "./ScansTable.jsx";
import ScanDetails from "./ScanDetails.jsx";
import Compare from "./Compare.jsx";
import ReportView from "./ReportView.jsx";
import Monitoring from "./Monitoring.jsx";
import MonitorDetail from "./MonitorDetail.jsx";
import AnswerTracking from "./AnswerTracking.jsx";
import BillingView from "./BillingView.jsx";
import { UpgradeProvider, useUpgrade } from "./UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { navigate, queryParam } from "../auth/router.jsx";
import { Avatar } from "../auth/ui.jsx";
import ProfilePage from "../auth/pages/ProfilePage.jsx";
import TeamPage from "../auth/pages/TeamPage.jsx";
import OrganizationPage from "../auth/pages/OrganizationPage.jsx";

const NAV = [
  { id: "home", label: "Dashboard", icon: LayoutDashboard },
  { id: "scans", label: "Recent Scans", icon: ScanLine },
  { id: "monitoring", label: "Monitoring", icon: Activity },
  { id: "answer-tracking", label: "Answer Tracking", icon: MessageSquare },
  { id: "report", label: "AI Visibility Report", icon: FileText },
  { id: "compare", label: "Compare", icon: GitCompare },
  { id: "summary", label: "Website Summary", icon: Globe },
];
const ACCOUNT_NAV = [
  { id: "billing", label: "Billing", icon: CreditCard },
  { id: "profile", label: "Profile", icon: User },
  { id: "team", label: "Team", icon: Users },
  { id: "organization", label: "Organization", icon: Building2 },
];
const TITLES = {
  home: ["Dashboard", "Your AI-visibility overview"],
  scans: ["Recent Scans", "Every scan you have run"],
  monitoring: ["Monitoring", "Track AI visibility over time and get alerted on changes"],
  "monitoring-detail": ["Monitor", "Historical timeline, trends, changes and alerts"],
  "answer-tracking": ["AI Answer Tracking", "Track whether AI assistants mention and cite your brand"],
  report: ["AI Visibility Report", "What's wrong, why it matters, and how to fix it"],
  compare: ["Compare Scans", "Diff two scans signal by signal"],
  summary: ["Website Summary", "Per-domain rollup"],
  details: ["Scan Details", "Full signal report"],
  billing: ["Billing", "Your plan, usage, invoices and payments"],
  profile: ["Profile", "Your account settings"],
  team: ["Team Members", "Manage who can access this workspace"],
  organization: ["Organization Settings", "Your workspace details"],
};

export default function Dashboard(props) {
  // Provider fetches /billing/subscription once and shares plan + usage with the
  // sidebar meter and every gated view (monitoring, compare, report).
  return (
    <UpgradeProvider>
      <DashboardBody {...props} />
    </UpgradeProvider>
  );
}

function DashboardBody({ onRunScan, onExit }) {
  const { user, org, role, logout, hasPermission } = useAuth();
  const { isLimited, meteredScans, usage, openUpgrade, handleGated, reloadSubscription } = useUpgrade();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");
  const [view, setView] = useState("home");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [scans, setScans] = useState([]);
  const [detail, setDetail] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [reportScanId, setReportScanId] = useState(null);   // scan whose report is shown
  const [monitorId, setMonitorId] = useState(null);         // monitor whose detail is shown

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [d, s] = await Promise.all([getDashboard(), getScans()]);
      setDashboard(d); setScans(s);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load the dashboard.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openDetail = useCallback(async (id) => {
    setView("details"); setDetail(null); setError(null);
    try { setDetail(await getScanDetail(id)); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not load that scan."); }
  }, []);

  const openReport = useCallback((id) => { setReportScanId(id); setView("report"); }, []);
  const openMonitor = useCallback((id) => { setMonitorId(id); setView("monitoring-detail"); }, []);

  // Deep link from a background (bulk) scan (navigate `/app?scan=<id>`): open that
  // scan's detail view, which renders live progress and then the completed report.
  useEffect(() => {
    const sid = queryParam("scan");
    if (sid) { navigate(window.location.pathname, { replace: true }); openDetail(sid); }
  }, [openDetail]);

  // Re-fetch a scan's detail (used when a background bulk scan finishes polling).
  const refreshDetail = useCallback(async (id) => {
    try { setDetail(await getScanDetail(id)); reloadSubscription(); }
    catch { /* keep the current view; the poller will retry */ }
  }, [reloadSubscription]);

  // Retry a failed bulk scan: re-submit the same URL list (from the failed scan's
  // per-page rows) and open the new (pending) scan so the user watches it from the top.
  const retryBulkScan = useCallback(async (scan) => {
    const urls = (scan?.bulk?.pages || []).map((p) => p.url).filter(Boolean);
    if (!urls.length) return;
    try {
      const res = await bulkScanUrls(urls);
      if (res?.scan_id) { await load(); openDetail(res.scan_id); }
    } catch (e) {
      if (!handleGated(e, "scans")) setError(e instanceof ScanError ? e.message : "Could not restart the scan.");
    }
  }, [load, openDetail, handleGated]);
  // Preselect two scans in Compare via the URL (Compare reads ?compare=a,b).
  const openCompare = useCallback((a, b) => {
    navigate(`${window.location.pathname}?compare=${a},${b}`);
    setView("compare");
  }, []);

  const onRerun = useCallback(async (id) => {
    setBusyId(id);
    try {
      const fresh = await rerunScan(id);
      await load();
      reloadSubscription();   // a re-run consumes a scan; refresh the meter
      await openDetail(fresh.scan_id);
    } catch (e) {
      // A monthly-scan-limit 402 opens the upgrade modal instead of a plain error.
      if (!handleGated(e, "scans")) setError(e instanceof ScanError ? e.message : "Re-run failed.");
    } finally { setBusyId(null); }
  }, [load, openDetail, handleGated, reloadSubscription]);

  const onDelete = useCallback(async (id) => {
    if (!window.confirm("Delete this scan? This cannot be undone.")) return;
    setBusyId(id);
    try {
      await deleteScan(id);
      setScans((prev) => prev.filter((s) => s.id !== id));
      getDashboard().then(setDashboard).catch(() => {});
      if (detail?.scan_id === id) { setDetail(null); setView("scans"); }
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Delete failed.");
    } finally { setBusyId(null); }
  }, [detail]);

  const [title, subtitle] = TITLES[view] || TITLES.home;
  // Views that manage their own data/empty states (never gated by the scan list).
  const STANDALONE_VIEWS = ["billing", "profile", "team", "organization", "monitoring", "monitoring-detail"];
  const isStandalone = STANDALONE_VIEWS.includes(view);
  const isEmpty = !loading && !error && scans.length === 0 && !isStandalone;

  return (
    <div className="dash">
      <aside className="dash-side">
        <div className="dash-brand"><Radar size={18} /> AEOMirror</div>
        <nav className="dash-nav">
          {NAV.map((n) => (
            <button key={n.id} className={view === n.id ? "on" : ""}
                    onClick={() => {
                      if (n.id === "report") setReportScanId(null);
                      // Opening Compare from the nav starts fresh (drop any ?compare= preset).
                      if (n.id === "compare" && window.location.search) navigate(window.location.pathname, { replace: true });
                      setView(n.id);
                    }}>
              <n.icon size={16} /> <span>{n.label}</span>
            </button>
          ))}
          <div style={{ height: 1, background: "var(--line)", margin: "8px 6px" }} />
          {ACCOUNT_NAV.map((n) => (
            <button key={n.id} className={view === n.id ? "on" : ""} onClick={() => setView(n.id)}>
              <n.icon size={16} /> <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="dash-side-foot">
          {meteredScans && <SideScanMeter scans={usage?.scans} onUpgrade={isLimited ? () => openUpgrade("scans") : null} />}
          <button className="dash-newscan" onClick={onRunScan}><Radar size={15} /> New scan</button>
          {user?.is_platform_admin && (
            <button className="dash-exit" onClick={() => navigate("/admin")}><Shield size={14} /> Admin panel</button>
          )}
          <button className="dash-exit" onClick={onExit}><ArrowLeft size={14} /> Homepage scanner</button>
          <div className="dash-user">
            <Avatar user={user} size={32} />
            <div className="dash-user-id">
              <div className="dash-user-name">{user?.name}</div>
              <div className="dash-user-role">{role}{org ? ` · ${org.name}` : ""}</div>
            </div>
            <button className="dash-exit" style={{ padding: 8 }} title="Sign out"
                    onClick={async () => { await logout(); navigate("/"); }}><LogOut size={14} /></button>
          </div>
        </div>
      </aside>

      <main className="dash-main">
        <header className="dash-top">
          <div>
            <div className="dash-top-title">{title}</div>
            <div className="dash-top-sub">{subtitle}</div>
          </div>
        </header>

        <div className="dash-content" key={view}>
          {/* These views manage their own data + empty/loading/error states. */}
          {view === "billing" ? (
            <BillingView />
          ) : view === "profile" ? (
            <ProfilePage />
          ) : view === "team" ? (
            <TeamPage />
          ) : view === "organization" ? (
            <OrganizationPage />
          ) : view === "monitoring" ? (
            <Monitoring onOpenMonitor={openMonitor} />
          ) : view === "answer-tracking" ? (
            <AnswerTracking />
          ) : view === "monitoring-detail" ? (
            <MonitorDetail monitorId={monitorId} onBack={() => setView("monitoring")} onOpenReport={openReport} />
          ) : (
            <>
              {error && <ErrorState message={error} onRetry={load} />}
              {loading ? (
                <><StatsSkeleton /><TableSkeleton /></>
              ) : isEmpty ? (
                <EmptyState onRun={onRunScan} />
              ) : view === "home" ? (
                <DashboardHome data={dashboard} onOpenLatest={openDetail} />
              ) : view === "scans" ? (
                <ScansTable scans={scans} busyId={busyId} onView={openDetail} onRerun={onRerun}
                            onDelete={onDelete} canRun={canRun} canDelete={canDelete}
                            onCompareSelected={openCompare} />
              ) : view === "report" ? (
                <ReportView scanId={reportScanId || dashboard?.latest_scan?.id} />
              ) : view === "compare" ? (
                <Compare scans={scans} onCompare={compareScans} />
              ) : view === "summary" ? (
                <WebsiteSummaryView scans={scans} onView={openDetail} />
              ) : view === "details" ? (
                detail ? <ScanDetails scan={detail} onBack={() => setView("scans")} onRerun={onRerun}
                                      busy={busyId === detail.scan_id} canRun={canRun}
                                      onReport={() => openReport(detail.scan_id)}
                                      onRefresh={refreshDetail} onRetryBulk={retryBulkScan} />
                  : !error && <><TableSkeleton rows={4} /></>
              ) : null}
            </>
          )}
        </div>
      </main>
    </div>
  );
}

/* ---- sidebar scan-usage meter (shows the monthly scan-job cap: Free 1, Pro 15).
       The upgrade CTA renders only when onUpgrade is provided (Free). ---- */
function SideScanMeter({ scans, onUpgrade }) {
  if (!scans || scans.unlimited) return null;
  const pct = scans.limit ? Math.min(100, Math.round((scans.used / scans.limit) * 100)) : 0;
  const color = pct >= 100 ? "var(--bad)" : pct >= 80 ? "var(--warn)" : "var(--accent)";
  return (
    <div className="side-meter">
      <div className="side-meter-top">
        <span className="side-meter-lbl">Scan jobs</span>
        <span className="side-meter-n">{scans.used}/{scans.limit}</span>
      </div>
      <div className="side-meter-bar"><div style={{ width: `${pct}%`, background: color }} /></div>
      {onUpgrade && <button className="side-meter-up" onClick={onUpgrade}>Upgrade →</button>}
    </div>
  );
}

/* ---- inline view: website summary (per-domain rollup) ---- */
function WebsiteSummaryView({ scans, onView }) {
  const byDomain = {};
  for (const s of scans) {
    const g = byDomain[s.domain] || (byDomain[s.domain] = { domain: s.domain, scans: [], scores: [] });
    g.scans.push(s);
    if (s.overall_score != null) g.scores.push(s.overall_score);
  }
  const rows = Object.values(byDomain).map((g) => {
    const latest = g.scans.slice().sort((a, b) => new Date(b.scan_time) - new Date(a.scan_time))[0];
    return {
      ...g, latest, count: g.scans.length,
      best: g.scores.length ? Math.max(...g.scores) : null,
      worst: g.scores.length ? Math.min(...g.scores) : null,
    };
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
