/* Admin platform UI (Phase 8). Internal admin app at /admin — not shown to
   customers. Sidebar + views for dashboard, users, organizations, scans, monitors,
   analytics, system health, audit logs and settings/feature-flags. */
import React, { useState } from "react";
import {
  Shield, LayoutDashboard, Users, Building2, ScanLine, Activity, BarChart3,
  HeartPulse, ScrollText, Settings2, ArrowLeft, RefreshCw, Trash2, Play, Pause,
  Ban, CheckCircle2, Mail, KeyRound, FileText, Inbox, Eye, RotateCcw,
} from "lucide-react";
import {
  BarChart, Bar, AreaChart, Area, PieChart, Pie, Cell, CartesianGrid,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { admin } from "../api.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { navigate } from "../auth/router.jsx";
import {
  useAdminData, AdminStat, Badge, SearchInput, Pager, Loading, ErrorBox, EmptyRow,
  statusTone, fmtDateTime, fmtBytes,
} from "./ui.jsx";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "users", label: "Users", icon: Users },
  { id: "orgs", label: "Organizations", icon: Building2 },
  { id: "scans", label: "Scans", icon: ScanLine },
  { id: "monitors", label: "Monitors", icon: Activity },
  { id: "support", label: "Support Inbox", icon: Inbox },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "system", label: "System Health", icon: HeartPulse },
  { id: "audit", label: "Audit Logs", icon: ScrollText },
  { id: "settings", label: "Settings", icon: Settings2 },
];
const AXIS = { fill: "var(--txt-dim)", fontSize: 10 };
const TT = { contentStyle: { background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }, labelStyle: { color: "var(--txt-mid)" }, itemStyle: { color: "var(--txt)" } };
const PIE = ["#43C08A", "#E6A94A", "#E5615B", "#34D3E0", "#8A97A3"];

export default function AdminApp() {
  const { user } = useAuth();
  const [view, setView] = useState("dashboard");
  return (
    <div className="ad">
      <aside className="ad-side">
        <div className="ad-brand"><Shield size={18} /> AEOMirror <span className="ad-tag">ADMIN</span></div>
        <nav className="ad-nav">
          {NAV.map((n) => (
            <button key={n.id} className={view === n.id ? "on" : ""} onClick={() => setView(n.id)}>
              <n.icon size={16} /> <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="ad-side-foot">
          <div className="ad-me">{user?.name}<span className="ad-dim">platform admin</span></div>
          <button className="ad-exit" onClick={() => navigate("/app")}><ArrowLeft size={14} /> Back to app</button>
        </div>
      </aside>
      <main className="ad-main">
        {view === "dashboard" && <DashboardView />}
        {view === "users" && <UsersView />}
        {view === "orgs" && <OrgsView />}
        {view === "scans" && <ScansView />}
        {view === "monitors" && <MonitorsView />}
        {view === "support" && <SupportView />}
        {view === "analytics" && <AnalyticsView />}
        {view === "system" && <SystemView />}
        {view === "audit" && <AuditView />}
        {view === "settings" && <SettingsView />}
      </main>
    </div>
  );
}

function Header({ title, sub, right }) {
  return (
    <div className="ad-head">
      <div><div className="ad-h1">{title}</div>{sub && <div className="ad-sub">{sub}</div>}</div>
      {right}
    </div>
  );
}

/* ------------------------------- dashboard ------------------------------- */
function DashboardView() {
  const { data, error, loading, reload } = useAdminData(() => admin.dashboard());
  return (
    <>
      <Header title="Dashboard" sub="Platform-wide overview" right={<button className="ad-btn" onClick={reload}><RefreshCw size={14} /> Refresh</button>} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <div className="ad-stats">
          <AdminStat label="Total Users" value={data.total_users} />
          <AdminStat label="Active Users" value={data.active_users} tone="var(--good)" />
          <AdminStat label="Organizations" value={data.organizations} />
          <AdminStat label="Total Websites" value={data.total_websites} />
          <AdminStat label="Total Scans" value={data.total_scans} />
          <AdminStat label="Today's Scans" value={data.todays_scans} />
          <AdminStat label="Running Monitors" value={data.running_monitors} tone="var(--accent)" />
          <AdminStat label="Failed Jobs" value={data.failed_jobs} tone={data.failed_jobs ? "var(--bad)" : undefined} />
          <AdminStat label="Average AI Score" value={data.average_ai_score ?? "—"} />
          <AdminStat label="API Requests" value={data.api_requests} />
          <AdminStat label="System Health" value={<Badge tone={statusTone(data.system_health)}>{data.system_health}</Badge>} />
        </div>
      )}
    </>
  );
}

/* ------------------------------- users ------------------------------- */
function UsersView() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);
  const { data, error, loading, reload } = useAdminData(() => admin.users({ q, page, page_size: 20 }), [q, page]);

  const act = async (id, fn) => { setBusy(id); setNotice(null); try { const r = await fn(id); if (r?.dev_reset_token) setNotice(`Reset link token: ${r.dev_reset_token}`); await reload(); } catch (e) { setNotice(e.message); } finally { setBusy(null); } };
  const del = (id) => { if (window.confirm("Delete this user? This cannot be undone.")) act(id, admin.deleteUser); };

  return (
    <>
      <Header title="Users" sub="Search and manage every user" right={<SearchInput value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search name or email" />} />
      {notice && <div className="ad-notice">{notice}</div>}
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>Name</th><th>Email</th><th>Org</th><th>Role</th><th>Status</th><th>Verified</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={7} label="No users match." />}
              {data.items.map((u) => (
                <tr key={u.id}>
                  <td>{u.name}{u.is_platform_admin && <Badge tone="accent">admin</Badge>}</td>
                  <td className="ad-mono">{u.email}</td>
                  <td>{u.organization?.name || "—"}</td>
                  <td>{u.role}</td>
                  <td><Badge tone={statusTone(u.status)}>{u.status}</Badge></td>
                  <td>{u.email_verified ? <CheckCircle2 size={14} color="var(--good)" /> : "—"}</td>
                  <td><div className="ad-actions">
                    {u.status === "active"
                      ? <button className="ad-ic" title="Suspend" disabled={busy === u.id} onClick={() => act(u.id, admin.suspendUser)}><Ban size={13} /></button>
                      : <button className="ad-ic" title="Activate" disabled={busy === u.id} onClick={() => act(u.id, admin.activateUser)}><CheckCircle2 size={13} /></button>}
                    {!u.email_verified && <button className="ad-ic" title="Verify email" disabled={busy === u.id} onClick={() => act(u.id, admin.verifyEmail)}><Mail size={13} /></button>}
                    <button className="ad-ic" title="Reset password" disabled={busy === u.id} onClick={() => act(u.id, admin.resetPassword)}><KeyRound size={13} /></button>
                    <button className="ad-ic danger" title="Delete" disabled={busy === u.id} onClick={() => del(u.id)}><Trash2 size={13} /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

/* ------------------------------- organizations ------------------------------- */
function OrgsView() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(null);
  const { data, error, loading, reload } = useAdminData(() => admin.orgs({ q, page, page_size: 20 }), [q, page]);
  const del = async (id) => { if (!window.confirm("Delete this organization and ALL its data?")) return; setBusy(id); try { await admin.deleteOrg(id); await reload(); } finally { setBusy(null); } };
  return (
    <>
      <Header title="Organizations" sub="Members, usage and storage" right={<SearchInput value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search name or slug" />} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>Name</th><th>Slug</th><th>Members</th><th>Monitors</th><th>Scans</th><th>Storage</th><th>Plan</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={8} label="No organizations." />}
              {data.items.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td><td className="ad-mono ad-dim">{o.slug}</td>
                  <td>{o.members}</td><td>{o.monitors}</td><td>{o.scans}</td>
                  <td>{fmtBytes(o.storage_bytes)}</td><td><Badge>{o.plan}</Badge></td>
                  <td><div className="ad-actions"><button className="ad-ic danger" title="Delete org" disabled={busy === o.id} onClick={() => del(o.id)}><Trash2 size={13} /></button></div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

/* ------------------------------- scans ------------------------------- */
function ScansView() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(null);
  const [logs, setLogs] = useState(null);
  const { data, error, loading, reload } = useAdminData(() => admin.scans({ q, page, page_size: 20 }), [q, page]);
  const del = async (id) => { if (!window.confirm("Delete this scan and its report?")) return; setBusy(id); try { await admin.deleteScan(id); await reload(); } finally { setBusy(null); } };
  const rerun = async (id) => { setBusy(id); try { await admin.rerunScan(id); await reload(); } finally { setBusy(null); } };
  const showLogs = async (id) => { setLogs({ id, lines: null }); try { const r = await admin.scanLogs(id); setLogs({ id, lines: r.logs }); } catch (e) { setLogs({ id, lines: [e.message] }); } };
  return (
    <>
      <Header title="Scans" sub="Search, inspect and manage scans" right={<SearchInput value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search by domain" />} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>Domain</th><th>Overall</th><th>ARS</th><th>Scanner</th><th>Duration</th><th>Date</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={7} label="No scans match." />}
              {data.items.map((s) => (
                <tr key={s.id}>
                  <td className="ad-mono">{s.domain}</td>
                  <td>{s.overall_score ?? "—"}</td><td>{s.ars}</td>
                  <td className="ad-dim">{s.scanner_version || "—"}</td>
                  <td className="ad-dim">{s.duration_ms ? `${s.duration_ms}ms` : "—"}</td>
                  <td className="ad-dim">{fmtDateTime(s.created_at)}</td>
                  <td><div className="ad-actions">
                    <button className="ad-ic" title="Scanner logs" onClick={() => showLogs(s.id)}><FileText size={13} /></button>
                    <button className="ad-ic" title="Re-run" disabled={busy === s.id} onClick={() => rerun(s.id)}><RefreshCw size={13} className={busy === s.id ? "spin-slow" : ""} /></button>
                    <button className="ad-ic danger" title="Delete" disabled={busy === s.id} onClick={() => del(s.id)}><Trash2 size={13} /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}
      {logs && (
        <div className="ad-modal" onClick={() => setLogs(null)}>
          <div className="ad-modal-c" onClick={(e) => e.stopPropagation()}>
            <div className="ad-modal-h">Scanner logs <button className="ad-btn sm" onClick={() => setLogs(null)}>Close</button></div>
            <pre className="ad-logs">{logs.lines ? logs.lines.join("\n") : "Loading…"}</pre>
          </div>
        </div>
      )}
    </>
  );
}

/* ------------------------------- monitors ------------------------------- */
function MonitorsView() {
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(null);
  const { data, error, loading, reload } = useAdminData(() => admin.monitors({ page, page_size: 20 }), [page]);
  const act = async (id, fn) => { setBusy(id); try { await fn(id); await reload(); } finally { setBusy(null); } };
  const del = (id) => { if (window.confirm("Delete this monitor and its history?")) act(id, admin.deleteMonitor); };
  return (
    <>
      <Header title="Monitors" sub="Every monitor across all organizations" right={<button className="ad-btn" onClick={reload}><RefreshCw size={14} /> Refresh</button>} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>Domain</th><th>Frequency</th><th>Status</th><th>Score</th><th>Alerts</th><th>Last scan</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={7} label="No monitors." />}
              {data.items.map((m) => (
                <tr key={m.id}>
                  <td className="ad-mono">{m.domain}</td><td>{m.frequency}</td>
                  <td><Badge tone={statusTone(m.status)}>{m.status}</Badge></td>
                  <td>{m.latest_score ?? "—"}</td>
                  <td>{m.open_alerts ? <Badge tone="bad">{m.open_alerts}</Badge> : "0"}</td>
                  <td className="ad-dim">{fmtDateTime(m.last_scan_at)}</td>
                  <td><div className="ad-actions">
                    <button className="ad-ic" title="Run now" disabled={busy === m.id} onClick={() => act(m.id, admin.runMonitor)}><Play size={13} /></button>
                    {m.status === "active"
                      ? <button className="ad-ic" title="Pause" disabled={busy === m.id} onClick={() => act(m.id, admin.pauseMonitor)}><Pause size={13} /></button>
                      : <button className="ad-ic" title="Resume" disabled={busy === m.id} onClick={() => act(m.id, admin.resumeMonitor)}><Play size={13} /></button>}
                    <button className="ad-ic danger" title="Delete" disabled={busy === m.id} onClick={() => del(m.id)}><Trash2 size={13} /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

/* ------------------------------- support inbox ------------------------------- */
const CONTACT_TABS = [
  { id: "", label: "All" },
  { id: "new", label: "New" },
  { id: "open", label: "Open" },
  { id: "closed", label: "Closed" },
];
const contactTone = (s) => ({ new: "accent", open: "warn", closed: "good" }[s] || "neutral");

function SupportView() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(null);
  const [selected, setSelected] = useState(null);   // contact being viewed
  const { data, error, loading, reload } = useAdminData(
    () => admin.contacts({ q, status, page, page_size: 20 }), [q, status, page]);

  const counts = data?.counts || {};
  const act = async (fn) => { setBusy(true); try { await fn(); await reload(); } finally { setBusy(false); } };
  const setStat = (id, s) => act(() => admin.setContactStatus(id, s));
  const del = (id) => { if (window.confirm("Delete this message? This cannot be undone.")) act(async () => { await admin.deleteContact(id); if (selected?.id === id) setSelected(null); }); };

  return (
    <>
      <Header title="Support Inbox" sub="Messages from the contact form"
        right={<SearchInput value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search name, email or subject" />} />

      <div className="ad-tabs">
        {CONTACT_TABS.map((t) => (
          <button key={t.id || "all"} className={status === t.id ? "on" : ""}
            onClick={() => { setPage(1); setStatus(t.id); }}>
            {t.label}{t.id && counts[t.id] != null ? <span className="ad-tabn">{counts[t.id]}</span> : null}
          </button>
        ))}
      </div>

      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>From</th><th>Subject</th><th>Status</th><th>Received</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={5} label="No messages." />}
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td><div className="ad-strong">{c.name}</div><div className="ad-dim ad-mono">{c.email}</div></td>
                  <td>{c.subject}</td>
                  <td><Badge tone={contactTone(c.status)}>{c.status}</Badge></td>
                  <td className="ad-dim">{fmtDateTime(c.created_at)}</td>
                  <td><div className="ad-actions">
                    <button className="ad-ic" title="View message" onClick={() => setSelected(c)}><Eye size={13} /></button>
                    {c.status !== "closed"
                      ? <button className="ad-ic" title="Mark resolved" disabled={busy} onClick={() => setStat(c.id, "closed")}><CheckCircle2 size={13} /></button>
                      : <button className="ad-ic" title="Reopen" disabled={busy} onClick={() => setStat(c.id, "open")}><RotateCcw size={13} /></button>}
                    <button className="ad-ic danger" title="Delete" disabled={busy} onClick={() => del(c.id)}><Trash2 size={13} /></button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}

      {selected && (
        <div className="ad-modal" onClick={() => setSelected(null)}>
          <div className="ad-modal-c" onClick={(e) => e.stopPropagation()}>
            <div className="ad-modal-h">
              <span>Message <Badge tone={contactTone(selected.status)}>{selected.status}</Badge></span>
              <button className="ad-btn sm" onClick={() => setSelected(null)}>Close</button>
            </div>
            <div className="ad-contact-meta">
              <div><span className="ad-dim">From</span> <b>{selected.name}</b></div>
              <div><span className="ad-dim">Email</span> <a className="ad-link" href={`mailto:${selected.email}`}>{selected.email}</a></div>
              {selected.website && <div><span className="ad-dim">Website</span> <a className="ad-link" href={selected.website} target="_blank" rel="noreferrer noopener">{selected.website}</a></div>}
              <div><span className="ad-dim">Subject</span> <b>{selected.subject}</b></div>
              <div><span className="ad-dim">Received</span> {fmtDateTime(selected.created_at)}</div>
            </div>
            <pre className="ad-logs ad-contact-body">{selected.message}</pre>
            <div className="ad-modal-actions">
              <a className="ad-btn" href={`mailto:${selected.email}?subject=Re: ${encodeURIComponent(selected.subject)}`}><Mail size={13} /> Reply</a>
              {selected.status !== "closed"
                ? <button className="ad-btn" disabled={busy} onClick={() => act(async () => { await admin.setContactStatus(selected.id, "closed"); setSelected({ ...selected, status: "closed" }); })}><CheckCircle2 size={13} /> Mark resolved</button>
                : <button className="ad-btn" disabled={busy} onClick={() => act(async () => { await admin.setContactStatus(selected.id, "open"); setSelected({ ...selected, status: "open" }); })}><RotateCcw size={13} /> Reopen</button>}
              <button className="ad-btn danger" disabled={busy} onClick={() => del(selected.id)}><Trash2 size={13} /> Delete</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ------------------------------- analytics ------------------------------- */
function AnalyticsView() {
  const { data, error, loading, reload } = useAdminData(() => admin.analytics());
  if (loading) return <><Header title="Analytics" /><Loading /></>;
  if (error) return <><Header title="Analytics" /><ErrorBox message={error} onRetry={reload} /></>;
  const dist = data.issue_distribution || {};
  const pieData = [{ name: "pass", value: dist.pass || 0 }, { name: "warn", value: dist.warn || 0 }, { name: "fail", value: dist.fail || 0 }];
  return (
    <>
      <Header title="Analytics" sub={`Across ${data.sample_size} recent scans`} />
      <div className="ad-stats">
        <AdminStat label="Scans today" value={data.scans.daily} />
        <AdminStat label="This week" value={data.scans.weekly} />
        <AdminStat label="This month" value={data.scans.monthly} />
        <AdminStat label="All time" value={data.scans.total} />
        <AdminStat label="Average score" value={data.average_score ?? "—"} />
      </div>
      <div className="ad-panel">
        <div className="ad-panel-h">Scans per day (14d)</div>
        <div className="ad-chart"><ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data.daily_series} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
            <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--accent)" stopOpacity={0} /></linearGradient></defs>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(d) => d.slice(5)} />
            <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip {...TT} /><Area type="monotone" dataKey="scans" stroke="var(--accent)" strokeWidth={2} fill="url(#ag)" />
          </AreaChart>
        </ResponsiveContainer></div>
      </div>
      <div className="ad-grid2">
        <div className="ad-panel">
          <div className="ad-panel-h">Signal status distribution</div>
          <div className="ad-chart"><ResponsiveContainer width="100%" height="100%">
            <PieChart><Pie data={pieData} dataKey="value" nameKey="name" innerRadius={40} outerRadius={70} paddingAngle={2}>
              {pieData.map((e, i) => <Cell key={i} fill={PIE[i]} />)}</Pie><Tooltip {...TT} /></PieChart>
          </ResponsiveContainer></div>
        </div>
        <div className="ad-panel">
          <div className="ad-panel-h">Top issue categories</div>
          <div className="ad-chart"><ResponsiveContainer width="100%" height="100%">
            <BarChart data={(data.top_issue_categories || []).slice(0, 6)} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 10 }}>
              <XAxis type="number" allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="label" width={100} tick={AXIS} axisLine={false} tickLine={false} />
              <Tooltip {...TT} cursor={{ fill: "var(--panel-2)" }} /><Bar dataKey="fail_count" fill="var(--bad)" radius={[0, 4, 4, 0]} barSize={13} />
            </BarChart>
          </ResponsiveContainer></div>
        </div>
      </div>
      <div className="ad-grid2">
        <div className="ad-panel">
          <div className="ad-panel-h">Most scanned industries</div>
          <table className="ad-table sm"><tbody>
            {(data.most_scanned_industries || []).slice(0, 8).map((r) => (
              <tr key={r.industry}><td>{r.industry}</td><td style={{ textAlign: "right" }} className="ad-mono">{r.count}</td></tr>
            ))}
          </tbody></table>
        </div>
        <div className="ad-panel">
          <div className="ad-panel-h">Most common failures</div>
          <table className="ad-table sm"><tbody>
            {(data.most_common_failures || []).slice(0, 8).map((r, i) => (
              <tr key={i}><td className="ad-dim">{r.issue}</td><td style={{ textAlign: "right" }} className="ad-mono">{r.count}</td></tr>
            ))}
          </tbody></table>
        </div>
      </div>
    </>
  );
}

/* ------------------------------- system health ------------------------------- */
function SystemView() {
  const { data, error, loading, reload } = useAdminData(() => admin.system());
  return (
    <>
      <Header title="System Health" sub="Live infrastructure status" right={<button className="ad-btn" onClick={reload}><RefreshCw size={14} /> Refresh</button>} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-health">
            <HealthCard label="Overall" value={data.overall} />
            <HealthCard label="Database" value={data.database} />
            <HealthCard label="Redis" value={data.redis} />
            <HealthCard label="Queue" value={data.queue.status} extra={`${data.queue.pending} pending · ${data.queue.running} running · ${data.queue.failed} failed`} />
            <HealthCard label="Scheduler" value={data.scheduler} />
            <HealthCard label="Worker" value={data.worker} />
            <HealthCard label="Email" value={data.email} />
          </div>
          <div className="ad-stats">
            <AdminStat label="API latency" value={`${data.api_latency_ms ?? 0} ms`} />
            <AdminStat label="API requests" value={data.api_requests ?? 0} />
            <AdminStat label="Memory (RSS)" value={data.memory_mb != null ? `${data.memory_mb} MB` : "—"} />
            <AdminStat label="Disk used" value={data.disk ? `${data.disk.percent_used}%` : "—"} sub={data.disk ? `${data.disk.used_gb} / ${data.disk.total_gb} GB` : ""} />
          </div>
        </>
      )}
    </>
  );
}
function HealthCard({ label, value, extra }) {
  return <div className="ad-hcard"><div className="ad-hcard-l">{label}</div><Badge tone={statusTone(value)}>{value}</Badge>{extra && <div className="ad-hcard-x">{extra}</div>}</div>;
}

/* ------------------------------- audit logs ------------------------------- */
function AuditView() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const { data, error, loading, reload } = useAdminData(() => admin.logs({ action, page, page_size: 40 }), [action, page]);
  return (
    <>
      <Header title="Audit Logs" sub="Privileged admin actions" right={
        <select className="ad-select" value={action} onChange={(e) => { setPage(1); setAction(e.target.value); }}>
          <option value="">All actions</option>
          {["admin_login", "delete_user", "suspend_user", "delete_scan", "rerun_scan", "delete_organization", "delete_monitor", "settings_change", "feature_flag_change", "permission_change"].map((a) => <option key={a} value={a}>{a}</option>)}
        </select>} />
      {loading ? <Loading /> : error ? <ErrorBox message={error} onRetry={reload} /> : (
        <>
          <div className="ad-tablewrap"><table className="ad-table">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
            <tbody>
              {data.items.length === 0 && <EmptyRow cols={5} label="No audit entries." />}
              {data.items.map((l) => (
                <tr key={l.id}>
                  <td className="ad-dim">{fmtDateTime(l.created_at)}</td>
                  <td className="ad-mono">{l.actor_email || "—"}</td>
                  <td><Badge>{l.action}</Badge></td>
                  <td className="ad-dim">{l.target_type ? `${l.target_type}:${(l.target_id || "").slice(0, 8)}` : "—"}</td>
                  <td className="ad-dim ad-mono" style={{ fontSize: 11 }}>{l.meta && Object.keys(l.meta).length ? JSON.stringify(l.meta) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

/* ------------------------------- settings + flags ------------------------------- */
function SettingsView() {
  const { data, error, loading, reload } = useAdminData(() => admin.getSettings());
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);

  const toggleFlag = async (name, enabled) => {
    setSaving(true); setNotice(null);
    try { await admin.updateSettings({ feature_flags: { [name]: enabled } }); await reload(); setNotice("Feature flags updated."); }
    catch (e) { setNotice(e.message); } finally { setSaving(false); }
  };
  const saveSetting = async (key, value) => {
    setSaving(true); setNotice(null);
    try { await admin.updateSettings({ settings: { [key]: value } }); await reload(); setNotice("Settings saved."); }
    catch (e) { setNotice(e.message); } finally { setSaving(false); }
  };

  if (loading) return <><Header title="Settings" /><Loading /></>;
  if (error) return <><Header title="Settings" /><ErrorBox message={error} onRetry={reload} /></>;
  const s = data.settings || {};
  const flags = data.feature_flags || {};
  return (
    <>
      <Header title="Settings" sub="Feature flags, maintenance and configuration" />
      {notice && <div className="ad-notice">{notice}</div>}

      <div className="ad-panel">
        <div className="ad-panel-h">Feature flags</div>
        <div className="ad-flags">
          {(data.flag_names || Object.keys(flags)).map((name) => (
            <label key={name} className="ad-flag">
              <input type="checkbox" checked={!!flags[name]} disabled={saving} onChange={(e) => toggleFlag(name, e.target.checked)} />
              <span>{name.replace(/_/g, " ")}</span>
              <Badge tone={flags[name] ? "good" : "warn"}>{flags[name] ? "on" : "off"}</Badge>
            </label>
          ))}
        </div>
      </div>

      <div className="ad-panel">
        <div className="ad-panel-h">Maintenance mode</div>
        <label className="ad-flag">
          <input type="checkbox" checked={!!s.maintenance_mode} disabled={saving} onChange={(e) => saveSetting("maintenance_mode", e.target.checked)} />
          <span>Enable maintenance mode</span>
          <Badge tone={s.maintenance_mode ? "bad" : "good"}>{s.maintenance_mode ? "ON — scanning blocked" : "off"}</Badge>
        </label>
        <div className="ad-dim" style={{ fontSize: 12, marginTop: 8 }}>When on, only platform admins can run scans and create monitors.</div>
      </div>

      <div className="ad-panel">
        <div className="ad-panel-h">Configuration</div>
        <SettingRow label="Global scanner version" value={s.scanner_version} onSave={(v) => saveSetting("scanner_version", v)} saving={saving} />
        <SettingRow label="Default rubric" value={s.default_rubric || ""} onSave={(v) => saveSetting("default_rubric", v || null)} saving={saving} />
        <SettingRow label="Email from name" value={s.email_from_name} onSave={(v) => saveSetting("email_from_name", v)} saving={saving} />
        <div className="ad-dim" style={{ fontSize: 12, marginTop: 6 }}>Email templates &amp; app config: {JSON.stringify(s.email_templates)} · {JSON.stringify(s.app_config)}</div>
      </div>
    </>
  );
}
function SettingRow({ label, value, onSave, saving }) {
  const [v, setV] = useState(value ?? "");
  return (
    <div className="ad-setrow">
      <span className="ad-setlabel">{label}</span>
      <input className="ad-input" value={v} onChange={(e) => setV(e.target.value)} />
      <button className="ad-btn sm" disabled={saving || v === (value ?? "")} onClick={() => onSave(v)}>Save</button>
    </div>
  );
}
