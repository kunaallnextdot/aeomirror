/* Authenticated app layout: the sidebar (rendered once) + a shared data context, with
   the active view supplied by the nested route via <Outlet/>. The active nav item is
   derived from the URL, not local state. Shared scan/dashboard data + mutation actions
   are passed to route children through the Outlet context so behaviour matches the old
   single-component dashboard (one load, shared across views). */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Radar, LayoutDashboard, ScanLine, GitCompare, Globe, ArrowLeft, FileText,
  Activity, User, Users, Building2, LogOut, Shield, CreditCard, MessageSquare,
} from "lucide-react";
import {
  getDashboard, getScans, deleteScan, rerunScan, bulkScanUrls, ScanError,
} from "../api.js";
import { UpgradeProvider, useUpgrade } from "../dashboard/UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { Avatar } from "../auth/ui.jsx";
import { RouteErrorBoundary, InLayoutErrorState } from "./RouteErrorBoundary.jsx";

const NAV = [
  { to: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/app/scans", label: "Recent Scans", icon: ScanLine },
  { to: "/app/monitoring", label: "Monitoring", icon: Activity },
  { to: "/app/answer-tracking", label: "Answer Tracking", icon: MessageSquare },
  { to: "/app/report", label: "AI Visibility Report", icon: FileText },
  { to: "/app/compare", label: "Compare", icon: GitCompare },
  { to: "/app/website-summary", label: "Website Summary", icon: Globe },
];
const ACCOUNT_NAV = [
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/profile", label: "Profile", icon: User },
  { to: "/app/team", label: "Team", icon: Users },
  { to: "/app/organization", label: "Organization", icon: Building2 },
];

// [title, subtitle] + document title, derived from the pathname (not state).
function routeMeta(pathname) {
  const p = pathname.replace(/\/+$/, "");
  const seg = p.split("/")[2] || "dashboard";     // segment after /app
  const isDetail = p.split("/").length > 3;
  const M = {
    dashboard: ["Dashboard", "Your AI-visibility overview"],
    scans: isDetail ? ["Scan Details", "Full signal report"] : ["Recent Scans", "Every scan you have run"],
    monitoring: isDetail ? ["Monitor", "Historical timeline, trends, changes and alerts"]
                         : ["Monitoring", "Track AI visibility over time and get alerted on changes"],
    "answer-tracking": ["AI Answer Tracking", "Track whether AI assistants mention and cite your brand"],
    report: ["AI Visibility Report", "What's wrong, why it matters, and how to fix it"],
    compare: ["Compare Scans", "Diff two scans signal by signal"],
    "website-summary": ["Website Summary", "Per-domain rollup"],
    billing: ["Billing", "Your plan, usage, invoices and payments"],
    profile: ["Profile", "Your account settings"],
    team: ["Team Members", "Manage who can access this workspace"],
    organization: ["Organization Settings", "Your workspace details"],
  };
  return M[seg] || ["Not found", ""];
}

export default function AppLayout() {
  return (
    <UpgradeProvider>
      <LayoutBody />
    </UpgradeProvider>
  );
}

function LayoutBody() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, org, role, logout, hasPermission } = useAuth();
  const { isLimited, meteredScans, usage, openUpgrade, handleGated, reloadSubscription } = useUpgrade();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [scans, setScans] = useState([]);
  const [busyId, setBusyId] = useState(null);

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

  // URL is the source of truth for a selection — these just navigate.
  const openDetail = useCallback((id) => navigate(`/app/scans/${encodeURIComponent(id)}`), [navigate]);
  const openReport = useCallback((id) =>
    navigate(id ? `/app/report?scan=${encodeURIComponent(id)}` : "/app/report"), [navigate]);
  const openMonitor = useCallback((id) => navigate(`/app/monitoring/${encodeURIComponent(id)}`), [navigate]);
  const openCompare = useCallback((a, b) => navigate(`/app/compare?compare=${a},${b}`), [navigate]);

  const onRerun = useCallback(async (id) => {
    setBusyId(id);
    try {
      const fresh = await rerunScan(id);
      await load();
      reloadSubscription();
      navigate(`/app/scans/${encodeURIComponent(fresh.scan_id)}`);
    } catch (e) {
      if (!handleGated(e, "scans")) setError(e instanceof ScanError ? e.message : "Re-run failed.");
    } finally { setBusyId(null); }
  }, [load, handleGated, reloadSubscription, navigate]);

  const onDelete = useCallback(async (id) => {
    if (!window.confirm("Delete this scan? This cannot be undone.")) return;
    setBusyId(id);
    try {
      await deleteScan(id);
      setScans((prev) => prev.filter((s) => s.id !== id));
      getDashboard().then(setDashboard).catch(() => {});
      if (location.pathname.includes(`/app/scans/${id}`)) navigate("/app/scans");
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Delete failed.");
    } finally { setBusyId(null); }
  }, [location.pathname, navigate]);

  // Retry a failed bulk scan: resubmit its URL list, open the new scan.
  const retryBulkScan = useCallback(async (scan) => {
    const urls = (scan?.bulk?.pages || []).map((p) => p.url).filter(Boolean);
    if (!urls.length) return;
    try {
      const res = await bulkScanUrls(urls);
      if (res?.scan_id) { await load(); navigate(`/app/scans/${encodeURIComponent(res.scan_id)}`); }
    } catch (e) {
      if (!handleGated(e, "scans")) setError(e instanceof ScanError ? e.message : "Could not restart the scan.");
    }
  }, [load, handleGated, navigate]);

  const onRunScan = useCallback(() => navigate("/"), [navigate]);

  // Deep link from a background (bulk) scan (?scan=<id> on any /app URL): open its detail.
  useEffect(() => {
    const sid = new URLSearchParams(location.search).get("scan");
    if (sid && !location.pathname.startsWith("/app/report")) {
      navigate(`/app/scans/${encodeURIComponent(sid)}`, { replace: true });
    }
  }, [location.search, location.pathname, navigate]);

  const [title, subtitle] = routeMeta(location.pathname);
  useEffect(() => { document.title = `${title} · AEOMirror`; }, [title]);

  const ctx = useMemo(() => ({
    dashboard, scans, loading, error, reload: load, busyId,
    canRun, canDelete, onRerun, onDelete, retryBulkScan,
    openDetail, openReport, openMonitor, openCompare, onRunScan,
    handleGated, reloadSubscription,
  }), [dashboard, scans, loading, error, load, busyId, canRun, canDelete, onRerun, onDelete,
       retryBulkScan, openDetail, openReport, openMonitor, openCompare, onRunScan,
       handleGated, reloadSubscription]);

  return (
    <div className="dash">
      <aside className="dash-side">
        <div className="dash-brand"><Radar size={18} /> AEOMirror</div>
        <nav className="dash-nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => "dash-nav-item" + (isActive ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </NavLink>
          ))}
          <div style={{ height: 1, background: "var(--line)", margin: "8px 6px" }} />
          {ACCOUNT_NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => "dash-nav-item" + (isActive ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="dash-side-foot">
          {meteredScans && <SideScanMeter scans={usage?.scans} onUpgrade={isLimited ? () => openUpgrade("scans") : null} />}
          <button className="dash-newscan" onClick={onRunScan}><Radar size={15} /> New scan</button>
          {user?.is_platform_admin && (
            <button className="dash-exit" onClick={() => navigate("/admin")}><Shield size={14} /> Admin panel</button>
          )}
          <button className="dash-exit" onClick={() => navigate("/")}><ArrowLeft size={14} /> Homepage scanner</button>
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
        <div className="dash-content" key={location.pathname}>
          {/* Keyed by pathname so navigating clears any prior error. Runtime errors in a
              view render in-layout (sidebar stays usable); a stale-chunk error recovers
              via the guarded reload. */}
          <RouteErrorBoundary fallback={<InLayoutErrorState />}>
            <Outlet context={ctx} />
          </RouteErrorBoundary>
        </div>
      </main>
    </div>
  );
}

/* sidebar scan-usage meter (monthly scan-job cap). */
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
