/* Authenticated app layout: the sidebar (rendered once) + a shared data context, with
   the active view supplied by the nested route via <Outlet/>. The active nav item is
   derived from the URL, not local state. Shared scan/dashboard data + mutation actions
   are passed to route children through the Outlet context so behaviour matches the old
   single-component dashboard (one load, shared across views). */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Radar, LayoutDashboard, ScanLine, GitCompare, Globe, ArrowLeft, FileText, FileSearch,
  Activity, User, Users, Building2, LogOut, Shield, CreditCard, MessageSquare, Mail, ListChecks,
} from "lucide-react";
import {
  getDashboard, getScans, deleteScan, rerunScan, bulkScanUrls, ScanError,
} from "../api.js";
import { UpgradeProvider, useUpgrade } from "../dashboard/UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { AuAvatar } from "../dashboard/aurora.jsx";
import { RouteErrorBoundary, InLayoutErrorState } from "./RouteErrorBoundary.jsx";
import "./AppLayout.aurora.css";

const SUPPORT_EMAIL = "aeomirror.support@gmail.com";

// Phase K: "View Report" now means what a new user actually expects after a scan —
// the negative-first diagnosis (score/100, top problems, Recommendations — ReportView,
// via /app/report). It previously pointed to /app/scans/latest (Scan Details' per-signal
// breakdown), which is a real, still-useful view, just not what "View Report" implies —
// that destination stays reachable under its own honest label, "Scan Details", rather
// than being removed. No route was added, removed, or renamed; only these two entries'
// `to`/`label` pairing changed.
export const NAV = [
  { to: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/app/scans", label: "Recent Scans", icon: ScanLine },
  { to: "/app/report", label: "View Report", icon: FileSearch },
  { to: "/app/action-center", label: "Action Center", icon: ListChecks },
  { to: "/app/monitoring", label: "Monitoring", icon: Activity },
  { to: "/app/answer-tracking", label: "Answer Tracking", icon: MessageSquare },
  { to: "/app/scans/latest", label: "Scan Details", icon: FileText },
  { to: "/app/compare", label: "Compare", icon: GitCompare },
  { to: "/app/website-summary", label: "Websites", icon: Globe },
];
const ACCOUNT_NAV = [
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/profile", label: "Profile", icon: User },
  { to: "/app/team", label: "Team", icon: Users },
  { to: "/app/organization", label: "Organization", icon: Building2 },
];

const FOOTER_PRODUCT = [
  { to: "/app/dashboard", label: "Dashboard" },
  { to: "/app/scans", label: "Recent Scans" },
  { to: "/app/action-center", label: "Action Center" },
  { to: "/app/report", label: "View Report" },
  { to: "/app/answer-tracking", label: "Answer Tracking" },
  { to: "/app/monitoring", label: "Monitoring" },
];
const FOOTER_RESOURCES = [
  { to: "/app/website-summary", label: "Websites" },
  { to: "/app/compare", label: "Compare" },
];
const FOOTER_ACCOUNT = [
  { to: "/app/profile", label: "Profile" },
  { to: "/app/organization", label: "Organization" },
  { to: "/app/billing", label: "Billing" },
];

// [title, subtitle] + document title, derived from the pathname (not state).
function routeMeta(pathname) {
  const p = pathname.replace(/\/+$/, "");
  const seg = p.split("/")[2] || "dashboard";     // segment after /app
  const isLatest = p.split("/")[3] === "latest";
  const isDetail = p.split("/").length > 3 && !isLatest;
  const M = {
    dashboard: ["Dashboard", "Your AI-visibility overview"],
    scans: isLatest ? ["Scan Details", "Your latest completed scan"]
                    : isDetail ? ["Scan Details", "Full diagnosis"] : ["Recent Scans", "Every scan you have run"],
    "action-center": ["Action Center", "What to fix next, in priority order"],
    monitoring: isDetail ? ["Monitor", "Historical timeline, trends, changes and alerts"]
                         : ["Monitoring", "Track AI visibility over time and get alerted on changes"],
    "answer-tracking": ["Answer Tracking", "Track whether AI assistants mention and cite your brand"],
    report: ["View Report", "What's wrong, why it matters, and how to fix it"],
    compare: ["Compare Scans", "Diff two scans signal by signal"],
    "website-summary": ["Websites", "Every domain you've scanned, with its latest and best/worst scores"],
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
    <div className="au-dash">
      <aside className="au-dash-side">
        <div className="au-dash-brand"><Radar size={18} /> AEOMirror</div>
        <nav className="au-dash-nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/app/scans"}
                     className={({ isActive }) => "au-dash-nav-item" + (isActive ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </NavLink>
          ))}
          <div style={{ height: 1, background: "var(--au-line)", margin: "8px 6px" }} />
          {ACCOUNT_NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => "au-dash-nav-item" + (isActive ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="au-dash-side-foot">
          {meteredScans && <SideScanMeter scans={usage?.scans} onUpgrade={isLimited ? () => openUpgrade("scans") : null} />}
          <button className="au-dash-newscan" onClick={onRunScan}><Radar size={15} /> New scan</button>
          {user?.is_platform_admin && (
            <button className="au-dash-exit" onClick={() => navigate("/admin")}><Shield size={14} /> Admin panel</button>
          )}
          <button className="au-dash-exit" onClick={() => navigate("/")}><ArrowLeft size={14} /> Homepage scanner</button>
          <div className="au-dash-user">
            <AuAvatar user={user} size={32} />
            <div className="au-dash-user-id">
              <div className="au-dash-user-name">{user?.name}</div>
              <div className="au-dash-user-role">{role}{org ? ` · ${org.name}` : ""}</div>
            </div>
            <button className="au-dash-exit" style={{ padding: 8 }} title="Sign out"
                    onClick={async () => { await logout(); navigate("/"); }}><LogOut size={14} /></button>
          </div>
        </div>
      </aside>

      <main className="au-dash-main">
        <header className="au-dash-top">
          <div>
            <div className="au-dash-top-title">{title}</div>
            <div className="au-dash-top-sub">{subtitle}</div>
          </div>
        </header>
        <div className="au-dash-content" key={location.pathname}>
          {/* Keyed by pathname so navigating clears any prior error. Runtime errors in a
              view render in-layout (sidebar stays usable); a stale-chunk error recovers
              via the guarded reload. */}
          <RouteErrorBoundary fallback={<InLayoutErrorState />}>
            <Outlet context={ctx} />
          </RouteErrorBoundary>
        </div>
        <AppFooter />
      </main>
    </div>
  );
}

/* App-shell footer — normal document flow (never fixed/sticky), pushed to the bottom
   of the viewport on short pages by `.au-dash-content{flex:1}` and simply following
   the content on long pages. Renders on every /app/* route (loading/error/empty
   states included) since it lives in the shell, not in any individual route view. */
function AppFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="au-dash-footer">
      <div className="au-dash-footer-top">
        <div className="au-dash-footer-brand">
          <div className="au-dash-footer-logo"><Radar size={15} /> AEOMirror</div>
          <p className="au-dash-footer-tag">AI visibility and AEO intelligence for modern websites.</p>
        </div>
        <FooterCol title="Product" items={FOOTER_PRODUCT} />
        <FooterCol title="Resources" items={FOOTER_RESOURCES}>
          <a className="au-dash-footer-link" href={`mailto:${SUPPORT_EMAIL}`}><Mail size={11} /> Help</a>
        </FooterCol>
        <FooterCol title="Account" items={FOOTER_ACCOUNT} />
      </div>
      <div className="au-dash-footer-bottom">© {year} AEOMirror. All rights reserved.</div>
    </footer>
  );
}

function FooterCol({ title, items, children }) {
  return (
    <div className="au-dash-footer-col">
      <div className="au-dash-footer-col-h">{title}</div>
      {items.map((it) => <Link key={it.to} className="au-dash-footer-link" to={it.to}>{it.label}</Link>)}
      {children}
    </div>
  );
}

/* sidebar scan-usage meter (monthly scan-job cap). */
function SideScanMeter({ scans, onUpgrade }) {
  if (!scans || scans.unlimited) return null;
  const pct = scans.limit ? Math.min(100, Math.round((scans.used / scans.limit) * 100)) : 0;
  const color = pct >= 100 ? "var(--au-peach-d)" : pct >= 80 ? "var(--au-lemon-d)" : "var(--au-primary)";
  return (
    <div className="au-side-meter">
      <div className="au-side-meter-top">
        <span className="au-side-meter-lbl">Billable scans</span>
        <span className="au-side-meter-n">{scans.used}/{scans.limit}</span>
      </div>
      <div className="au-side-meter-bar"><div style={{ width: `${pct}%`, background: color }} /></div>
      {onUpgrade && <button className="au-side-meter-up" onClick={onUpgrade}>Upgrade →</button>}
    </div>
  );
}
