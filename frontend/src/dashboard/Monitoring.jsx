/* Monitoring section — MIGRATED to Aurora. Data flow, polling (15s), handlers, groupAlerts,
   and gating logic are byte-for-byte unchanged; only JSX + class names changed. Self-contained
   loading/error states re-skinned inline. `.aurora-screen`-scoped. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Radar, Plus, Play, Pause, Trash2, ArrowUpRight, ArrowDownRight, Minus,
  Bell, AlertTriangle, CheckCircle2, Clock, ChevronRight, Lock, RefreshCw, Bot,
} from "lucide-react";
import {
  listMonitors, createMonitor, runMonitor, updateMonitor, deleteMonitor,
  listAlerts, acknowledgeAlert, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Shell, Cell, Ring, Button, Skeleton } from "./aurora.jsx";
import "./Monitoring.aurora.css";

const FREQS = ["daily", "weekly", "monthly", "manual"];
const SEV_COLOR = { critical: "var(--au-peach-d)", warning: "var(--au-lemon-d)", info: "var(--au-sky-d)" };

function TrendBadge({ trend }) {
  if (trend === "up") return <span className="au-mon-trend up"><ArrowUpRight size={14} /> improving</span>;
  if (trend === "down") return <span className="au-mon-trend down"><ArrowDownRight size={14} /> declining</span>;
  if (trend === "flat") return <span className="au-mon-trend flat"><Minus size={14} /> steady</span>;
  return <span className="au-mon-trend none"><Minus size={14} /> new</span>;
}

export default function Monitoring({ onOpenMonitor }) {
  const { hasPermission } = useAuth();
  const { isLimited, usage, openUpgrade, handleGated, reloadSubscription } = useUpgrade();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");

  const [data, setData] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [url, setUrl] = useState("");
  const [frequency, setFrequency] = useState("weekly");
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [formErr, setFormErr] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async (quiet) => {
    if (!quiet) setError(null);
    try {
      const [m, a] = await Promise.all([listMonitors(), listAlerts({ status: "open", limit: 8 })]);
      setData(m); setAlerts(a.alerts || []);
    } catch (e) {
      if (!quiet) setError(e instanceof ScanError ? e.message : "Could not load monitors.");
    }
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(() => load(true), 15000);   // live refresh
    return () => clearInterval(timer.current);
  }, [load]);

  const submit = async (e) => {
    e.preventDefault();
    setFormErr(null); setCreating(true);
    try {
      await createMonitor({ url, frequency, name: name || undefined });
      setUrl(""); setName(""); setShowForm(false);
      await load();
      reloadSubscription();   // monitor count changed → refresh the meter/lock
    } catch (ex) {
      if (!handleGated(ex, "monitors")) {
        setFormErr(ex instanceof ScanError ? ex.message : "Could not create the monitor.");
      }
    } finally { setCreating(false); }
  };

  const act = async (id, fn) => {
    setBusyId(id);
    try { await fn(); await load(); reloadSubscription(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Action failed."); }
    finally { setBusyId(null); }
  };

  const onRun = (id) => act(id, () => runMonitor(id));
  const onToggle = (m) => act(m.id, () => updateMonitor(m.id, { status: m.status === "active" ? "paused" : "active" }));
  const onDelete = (m) => {
    if (!window.confirm("Delete this monitor and its history?")) return;
    act(m.id, () => deleteMonitor(m.id));
  };
  const onAckMerged = async (a) => {
    const ids = a.ids || [a.id];
    setAlerts((prev) => prev.filter((x) => !ids.includes(x.id)));
    await Promise.all(ids.map((id) => acknowledgeAlert(id).catch(() => {})));
  };

  const body = () => {
    if (error) return (
      <Cell solid><div className="au-card-center" role="alert">
        <div className="au-ill au-ill-bad"><AlertTriangle size={26} /></div>
        <div className="au-card-s" style={{ marginBottom: 20 }}>{error}</div>
        <Button variant="accent" onClick={load}>Retry</Button>
      </div></Cell>
    );
    if (!data) return <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={72} />)}</div></Cell>;

    const monitors = data.monitors || [];
    const counts = data.counts || {};
    const active = monitors.filter((m) => m.status === "active");
    const paused = monitors.filter((m) => m.status === "paused");
    const monitorLimit = usage?.monitors?.limit;
    const monitorsLocked = isLimited && monitorLimit != null && (counts.total || 0) >= monitorLimit;

    return (
      <>
        {/* header */}
        <div className="au-mon-top">
          <div className="au-mon-stats">
            <div><div className="au-mon-stat-n">{counts.total || 0}</div><div className="au-mon-stat-l">monitors</div></div>
            <div><div className="au-mon-stat-n" style={{ color: "var(--au-mint-d)" }}>{counts.active || 0}</div><div className="au-mon-stat-l">active</div></div>
            <div><div className="au-mon-stat-n" style={{ color: "var(--au-muted)" }}>{counts.paused || 0}</div><div className="au-mon-stat-l">paused</div></div>
            <div><div className="au-mon-stat-n" style={{ color: counts.open_alerts ? "var(--au-peach-d)" : "var(--au-muted)" }}>{counts.open_alerts || 0}</div><div className="au-mon-stat-l">open alerts</div></div>
          </div>
          {canRun && (monitorsLocked ? (
            <Button variant="ghost" onClick={() => openUpgrade("monitors")}
                    title="Free plan monitor limit reached — upgrade for more">
              <Lock size={14} /> New monitor
            </Button>
          ) : (
            <Button variant="accent" onClick={() => setShowForm((s) => !s)}>
              <Plus size={15} /> New monitor
            </Button>
          ))}
        </div>

        {/* create form */}
        {showForm && canRun && (
          <Cell solid className="au-mon-form"><form onSubmit={submit}>
            {formErr && <div className="au-mon-formerr"><AlertTriangle size={13} /> {formErr}</div>}
            <div className="au-mon-form-row">
              <input className="au-input" style={{ flex: 1 }} placeholder="https://example.com" value={url} onChange={(e) => setUrl(e.target.value)} required />
              <input className="au-input" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} style={{ maxWidth: 200 }} />
              <select className="au-select" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
                {FREQS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              <Button type="submit" variant="accent" loading={creating} disabled={creating}>{creating ? "Adding…" : "Add"}</Button>
            </div>
            <div className="au-mon-fhint">Recurring monitors run their first scan within a minute, then on the chosen schedule.</div>
          </form></Cell>
        )}

        {/* open alerts strip */}
        {alerts.length > 0 && (
          <Cell solid className="au-mon-alerts">
            <div className="au-panel-h"><Bell size={14} style={{ color: "var(--au-peach-d)" }} /> Open alerts <span className="au-sub">{alerts.length} shown</span></div>
            {groupAlerts(alerts, monitors).map((grp) => (
              <div key={grp.monitorId} className="au-mon-alert-group">
                <div className="au-mon-alert-monitor">{grp.name}</div>
                {grp.items.map((a) => (
                  <div key={a.id} className="au-mon-alert">
                    <span className="au-mon-alert-dot" style={{ background: SEV_COLOR[a.severity] }} />
                    <div className="au-mon-alert-body">
                      <div className="au-mon-alert-title">
                        {a.title}
                        {a.count > 1 && <span className="au-mon-alert-count-badge" title={`${a.count} occurrences`}>×{a.count}</span>}
                      </div>
                      <div className="au-mon-alert-msg">{a.message}</div>
                    </div>
                    <span className="au-mon-alert-time">{fmtDate(a.created_at)}</span>
                    {canRun && <button className="au-iconbtn" title={a.count > 1 ? "Acknowledge all" : "Acknowledge"} onClick={() => onAckMerged(a)}><CheckCircle2 size={13} /></button>}
                  </div>
                ))}
              </div>
            ))}
          </Cell>
        )}

        {/* empty state */}
        {monitors.length === 0 ? (
          <Cell solid><div className="au-mon-empty">
            <Radar size={30} />
            <div className="au-mon-empty-t">No monitors yet</div>
            <div className="au-mon-empty-s">Create a monitor to track a site's AI visibility over time and get alerted when it changes.</div>
            {canRun && <Button variant="accent" onClick={() => setShowForm(true)}><Plus size={15} /> Create your first monitor</Button>}
          </div></Cell>
        ) : (
          <>
            {active.length > 0 && <div className="au-mon-section-h">Active</div>}
            <div className="au-mon-grid">
              {active.map((m) => (
                <MonitorCard key={m.id} m={m} busy={busyId === m.id} canRun={canRun} canDelete={canDelete}
                             onOpen={() => onOpenMonitor(m.id)} onRun={() => onRun(m.id)} onToggle={() => onToggle(m)} onDelete={() => onDelete(m)} />
              ))}
            </div>
            {paused.length > 0 && <div className="au-mon-section-h">Paused</div>}
            <div className="au-mon-grid">
              {paused.map((m) => (
                <MonitorCard key={m.id} m={m} busy={busyId === m.id} canRun={canRun} canDelete={canDelete}
                             onOpen={() => onOpenMonitor(m.id)} onRun={() => onRun(m.id)} onToggle={() => onToggle(m)} onDelete={() => onDelete(m)} />
              ))}
            </div>
          </>
        )}
      </>
    );
  };

  return <div className="aurora-screen"><Shell>{body()}</Shell></div>;
}

/* Group open alerts under their monitor, collapsing identical alerts to ×N (unchanged). */
function groupAlerts(alerts, monitors) {
  const nameById = Object.fromEntries((monitors || []).map((m) => [m.id, m.name || m.domain]));
  const groups = new Map();
  for (const a of alerts) {
    const mid = a.monitor_id || "—";
    if (!groups.has(mid)) groups.set(mid, { monitorId: mid, name: nameById[mid] || "Monitor", byKey: new Map() });
    const g = groups.get(mid);
    const key = `${a.title} ${a.message}`;
    const existing = g.byKey.get(key);
    if (existing) { existing.count += 1; existing.ids.push(a.id); }
    else g.byKey.set(key, { ...a, count: 1, ids: [a.id] });
  }
  return [...groups.values()].map((g) => ({
    monitorId: g.monitorId, name: g.name, items: [...g.byKey.values()],
  }));
}

function MonitorCard({ m, busy, canRun, canDelete, onOpen, onRun, onToggle, onDelete }) {
  return (
    <div className="au-mon-card">
      <div className="au-mon-card-top" onClick={onOpen} role="button">
        <span role="img" aria-label={`Score ${m.latest_score ?? "not available"}`}><Ring value={m.latest_score} size={52} stroke={6} /></span>
        <div className="au-mon-card-id">
          <div className="au-mon-card-name">
            <span className={`au-mon-status ${m.status}`} /> {m.name || m.domain}
          </div>
          <div className="au-mon-card-url">{m.domain}</div>
        </div>
        <ChevronRight size={16} className="au-dim" />
      </div>
      <div className="au-mon-card-meta">
        <TrendBadge trend={m.trend} />
        <span className="au-mon-freq">{m.frequency}</span>
        {m.open_alert_count > 0 && <span className="au-mon-alertcount"><Bell size={11} /> {m.open_alert_count}</span>}
        {m.critical_crawler_blocked && (
          <span className="au-mon-crawlerbadge" title="A critical AI crawler is blocked on this site">
            <Bot size={11} /> Crawler blocked
          </span>
        )}
      </div>
      <div className="au-mon-card-times">
        <span><Clock size={11} /> last {m.last_scan_at ? fmtDate(m.last_scan_at) : "—"}</span>
        <span>next {m.next_scan_at ? fmtDate(m.next_scan_at) : (m.frequency === "manual" ? "manual" : "—")}</span>
      </div>
      <div className="au-mon-card-actions">
        {canRun && <button className="au-iconbtn" disabled={busy} onClick={onRun} title="Run now"><RefreshCw size={13} className={busy ? "spin-slow" : ""} /></button>}
        {canRun && (m.status === "active"
          ? <button className="au-iconbtn" disabled={busy} onClick={onToggle} title="Pause"><Pause size={13} /></button>
          : <button className="au-iconbtn" disabled={busy} onClick={onToggle} title="Resume"><Play size={13} /> Resume</button>)}
        <button className="au-iconbtn" onClick={onOpen} title="Open">Details</button>
        {canDelete && <button className="au-iconbtn au-danger" disabled={busy} onClick={onDelete} title="Delete"><Trash2 size={13} /></button>}
      </div>
    </div>
  );
}
