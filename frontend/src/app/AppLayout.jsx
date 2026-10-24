/* Authenticated app layout: the sidebar (rendered once) + a shared data context, with
   the active view supplied by the nested route via <Outlet/>. The active nav item is
   derived from the URL, not local state. Shared scan/dashboard data + mutation actions
   are passed to route children through the Outlet context so behaviour matches the old
   single-component dashboard (one load, shared across views). */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Radar, LayoutDashboard, ScanLine, GitCompare, Globe, ArrowLeft, FileText, FileSearch,
  Activity, User, Users, Building2, LogOut, Shield, CreditCard, MessageSquare, Mail, ListChecks,
  BarChart3, TrendingUp,
} from "lucide-react";
import {
  getDashboard, getScans, deleteScan, rerunScan, bulkScanUrls, ScanError,
} from "../api.js";
import Logo from "../shared/Logo.jsx";
import { UpgradeProvider, useUpgrade } from "../dashboard/UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { SpAvatar } from "../shared/spectrum.jsx";
import { RouteErrorBoundary, InLayoutErrorState } from "./RouteErrorBoundary.jsx";
import "./AppLayout.spectrum.css";

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
  // `exact` so a scan detail page (/app/scans/:id) lights up "Scan Details" below,
  // never this one too — see isNavItemActive's own comment.
  { to: "/app/scans", label: "Recent Scans", icon: ScanLine, exact: true },
  { to: "/app/report", label: "View Report", icon: FileSearch },
  { to: "/app/action-center", label: "Action Center", icon: ListChecks },
  { to: "/app/monitoring", label: "Monitoring", icon: Activity },
  { to: "/app/answer-tracking", label: "Answer Tracking", icon: MessageSquare },
  { to: "/app/analytics/ai-traffic", label: "AI Traffic", icon: BarChart3 },
  { to: "/app/business-impact", label: "AI Business Impact", icon: TrendingUp },
  // `activeUnder` — the real route for this screen is scans/:scanId (ScanDetailRoute),
  // reached either via this exact link (scans/latest, which redirects to the latest
  // scan's id) or by opening any scan from elsewhere (e.g. Recent Scans' own list,
  // or a deep link). Either way the URL becomes /app/scans/<id>, never literally
  // /app/scans/latest again, so matching has to cover any id, not just this link's
  // own `to`.
  { to: "/app/scans/latest", label: "Scan Details", icon: FileText, activeUnder: "/app/scans" },
  { to: "/app/compare", label: "Compare", icon: GitCompare },
  { to: "/app/website-summary", label: "Websites", icon: Globe },
];
const ACCOUNT_NAV = [
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/profile", label: "Profile", icon: User },
  { to: "/app/team", label: "Team", icon: Users },
  { to: "/app/organization", label: "Organization", icon: Building2 },
];

// Single source of truth for sidebar active-state, used for every NAV/ACCOUNT_NAV item
// (replaces NavLink's own matching so every item's rule is explicit and in one place).
// Trailing slashes never matter (stripped before comparing, except the bare "/").
// Default rule: active on an exact match OR nested under `to` (e.g. Monitoring stays
// active on /app/monitoring/:monitorId and .../intelligence; Answer Tracking stays
// active on /app/answer-tracking/:monitorId/runs/:runId) — every current route besides
// Scans happens to have no OTHER nav item nested under it, so this is safe everywhere.
// `exact: true` opts out of the nested case (Recent Scans). `activeUnder` opts IN to
// matching only the nested case, not the base path itself (Scan Details).
export function isNavItemActive(pathname, item) {
  const p = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (item.activeUnder) return p.startsWith(item.activeUnder + "/");
  if (item.exact) return p === item.to;
  return p === item.to || p.startsWith(item.to + "/");
}

const FOOTER_PRODUCT = [
  { to: "/app/dashboard", label: "Dashboard" },
  { to: "/app/scans", label: "Recent Scans" },
  { to: "/app/action-center", label: "Action Center" },
  { to: "/app/report", label: "View Report" },
  { to: "/app/answer-tracking", label: "Answer Tracking" },
  { to: "/app/analytics/ai-traffic", label: "AI Traffic" },
  { to: "/app/business-impact", label: "AI Business Impact" },
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
  const isIntelligence = p.split("/")[4] === "intelligence";
  const M = {
    dashboard: ["Dashboard", "Your AI-visibility overview"],
    scans: isLatest ? ["Scan Details", "Your latest completed scan"]
                    : isDetail ? ["Scan Details", "Full diagnosis"] : ["Recent Scans", "Every scan you have run"],
    "action-center": ["Action Center", "What to fix next, in priority order"],
    monitoring: isIntelligence
      ? ["Site Intelligence", "Crawl, classify and prioritize every page with evidence-backed AI analysis"]
      : isDetail ? ["Monitor", "Historical timeline, trends, changes and alerts"]
                 : ["Monitoring", "Track AI visibility over time and get alerted on changes"],
    "answer-tracking": ["Answer Tracking", "Track whether AI assistants mention and cite your brand"],
    analytics: ["AI Traffic", "Real inbound visitor traffic from AI assistants, via your connected Google Analytics property"],
    "business-impact": ["AI Business Impact", "How much traffic, leads, bookings and revenue come from AI assistants"],
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
    <div className="sp-dash">
      <aside className="sp-dash-side">
        <Link to="/" className="sp-dash-brand" aria-label="Go to AEOMirror homepage"><Logo variant="lockup" size={18} /></Link>
        <nav className="sp-dash-nav">
          {NAV.map((n) => (
            <Link key={n.to} to={n.to}
                  className={"sp-dash-nav-item" + (isNavItemActive(location.pathname, n) ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </Link>
          ))}
          <div style={{ height: 1, background: "var(--sp-line)", margin: "8px 6px" }} />
          {ACCOUNT_NAV.map((n) => (
            <Link key={n.to} to={n.to}
                  className={"sp-dash-nav-item" + (isNavItemActive(location.pathname, n) ? " on" : "")}>
              <n.icon size={16} /> <span>{n.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sp-dash-side-foot">
          {meteredScans && <SideScanMeter scans={usage?.scans} onUpgrade={isLimited ? () => openUpgrade("scans") : null} />}
          <button className="sp-dash-newscan" onClick={onRunScan}><Radar size={15} /> New scan</button>
          {user?.is_platform_admin && (
            <button className="sp-dash-exit" onClick={() => navigate("/admin")}><Shield size={14} /> Admin panel</button>
          )}
          <button className="sp-dash-exit" onClick={() => navigate("/")}><ArrowLeft size={14} /> Homepage scanner</button>
          <div className="sp-dash-user">
            <SpAvatar user={user} size={32} />
            <div className="sp-dash-user-id">
              <div className="sp-dash-user-name">{user?.name}</div>
              <div className="sp-dash-user-role">{role}{org ? ` · ${org.name}` : ""}</div>
            </div>
            <button className="sp-dash-exit" style={{ padding: 8 }} title="Sign out"
                    onClick={async () => { await logout(); navigate("/"); }}><LogOut size={14} /></button>
          </div>
        </div>
      </aside>

      <main className="sp-dash-main">
        <header className="sp-dash-top">
          <div>
            <div className="sp-dash-top-title">{title}</div>
            <div className="sp-dash-top-sub">{subtitle}</div>
          </div>
        </header>
        <div className="sp-dash-content" key={location.pathname}>
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
    <footer className="sp-dash-footer">
      <div className="sp-dash-footer-top">
        <div className="sp-dash-footer-brand">
          <Link to="/" aria-label="Go to AEOMirror homepage"><Logo variant="lockup" size={15} /></Link>
          <p className="sp-dash-footer-tag">AI visibility and AEO intelligence for modern websites.</p>
        </div>
        <FooterCol title="Product" items={FOOTER_PRODUCT} />
        <FooterCol title="Resources" items={FOOTER_RESOURCES}>
          <a className="sp-dash-footer-link" href={`mailto:${SUPPORT_EMAIL}`}><Mail size={11} /> Help</a>
        </FooterCol>
        <FooterCol title="Account" items={FOOTER_ACCOUNT} />
      </div>
      <div className="sp-dash-footer-bottom">© {year} AEOMirror. All rights reserved.</div>
    </footer>
  );
}

function FooterCol({ title, items, children }) {
  return (
    <div className="sp-dash-footer-col">
      <div className="sp-dash-footer-col-h">{title}</div>
      {items.map((it) => <Link key={it.to} className="sp-dash-footer-link" to={it.to}>{it.label}</Link>)}
      {children}
    </div>
  );
}

/* sidebar scan-usage meter (monthly scan-job cap). No red/amber/green: at-limit ->
   violet (needs-work), near-limit -> lav (attention), otherwise the brand primary. */
function SideScanMeter({ scans, onUpgrade }) {
  if (!scans || scans.unlimited) return null;
  const pct = scans.limit ? Math.min(100, Math.round((scans.used / scans.limit) * 100)) : 0;
  const color = pct >= 100 ? "var(--sp-violet-d)" : pct >= 80 ? "var(--sp-lav-d)" : "var(--sp-primary)";
  return (
    <div className="sp-side-meter">
      <div className="sp-side-meter-top">
        <span className="sp-side-meter-lbl">Billable scans</span>
        <span className="sp-side-meter-n">{scans.used}/{scans.limit}</span>
      </div>
      <div className="sp-side-meter-bar"><div style={{ width: `${pct}%`, background: color }} /></div>
      {onUpgrade && <button className="sp-side-meter-up" onClick={onUpgrade}>Upgrade →</button>}
    </div>
  );
}
