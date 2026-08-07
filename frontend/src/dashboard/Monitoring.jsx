/* Monitoring section (Phase 7): active/paused monitors with live status, current
   score, trend indicator and alert counts; an open-alerts strip; and an add-monitor
   form. Polls every 15s so background scans surface without a manual refresh. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Radar, Plus, Play, Pause, Trash2, ArrowUpRight, ArrowDownRight, Minus,
  Bell, AlertTriangle, CheckCircle2, Clock, ChevronRight, Lock, RefreshCw,
} from "lucide-react";
import {
  listMonitors, createMonitor, runMonitor, updateMonitor, deleteMonitor,
  listAlerts, acknowledgeAlert, ScanError,
} from "../api.js";
import { ScoreRing, scoreColor, fmtDate, TableSkeleton, ErrorState } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";

const FREQS = ["daily", "weekly", "monthly", "manual"];
const SEV_COLOR = { critical: "var(--bad)", warning: "var(--warn)", info: "var(--accent)" };

function TrendBadge({ trend }) {
  if (trend === "up") return <span className="mon-trend up"><ArrowUpRight size={14} /> improving</span>;
  if (trend === "down") return <span className="mon-trend down"><ArrowDownRight size={14} /> declining</span>;
  if (trend === "flat") return <span className="mon-trend flat"><Minus size={14} /> steady</span>;
  return <span className="mon-trend none"><Minus size={14} /> new</span>;
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
      // Hitting the Free monitor cap opens the upgrade modal instead of a form error.
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
  // Acknowledge every alert merged into a collapsed row (a.ids holds all of them).
  const onAckMerged = async (a) => {
    const ids = a.ids || [a.id];
    setAlerts((prev) => prev.filter((x) => !ids.includes(x.id)));
    await Promise.all(ids.map((id) => acknowledgeAlert(id).catch(() => {})));
  };

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <TableSkeleton rows={5} />;

  const monitors = data.monitors || [];
  const counts = data.counts || {};
  const active = monitors.filter((m) => m.status === "active");
  const paused = monitors.filter((m) => m.status === "paused");
  // Free plans have a monitor cap. Keep the button visible but locked once reached
  // (measured against the live count, so it flips the moment you hit the limit).
  const monitorLimit = usage?.monitors?.limit;
  const monitorsLocked = isLimited && monitorLimit != null && (counts.total || 0) >= monitorLimit;

  return (
    <div className="mon">
      {/* header */}
      <div className="mon-top">
        <div className="mon-stats">
          <div className="mon-stat"><div className="mon-stat-n">{counts.total || 0}</div><div className="mon-stat-l">monitors</div></div>
          <div className="mon-stat"><div className="mon-stat-n" style={{ color: "var(--good)" }}>{counts.active || 0}</div><div className="mon-stat-l">active</div></div>
          <div className="mon-stat"><div className="mon-stat-n" style={{ color: "var(--txt-mid)" }}>{counts.paused || 0}</div><div className="mon-stat-l">paused</div></div>
          <div className="mon-stat"><div className="mon-stat-n" style={{ color: counts.open_alerts ? "var(--bad)" : "var(--txt-mid)" }}>{counts.open_alerts || 0}</div><div className="mon-stat-l">open alerts</div></div>
        </div>
        {canRun && (monitorsLocked ? (
          <button className="mon-add is-locked" onClick={() => openUpgrade("monitors")}
                  title="Free plan monitor limit reached — upgrade for more">
            <Lock size={14} className="lock-i" /> New monitor
          </button>
        ) : (
          <button className="mon-add" onClick={() => setShowForm((s) => !s)}>
            <Plus size={15} /> New monitor
          </button>
        ))}
      </div>

      {/* create form */}
      {showForm && canRun && (
        <form className="d-panel mon-form" onSubmit={submit}>
          {formErr && <div className="mon-formerr"><AlertTriangle size={13} /> {formErr}</div>}
          <div className="mon-form-row">
            <input className="f-input" placeholder="https://example.com" value={url} onChange={(e) => setUrl(e.target.value)} required />
            <input className="f-input" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} style={{ maxWidth: 200 }} />
            <select className="f-select" value={frequency} onChange={(e) => setFrequency(e.target.value)} style={{ padding: "10px 12px" }}>
              {FREQS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <button type="submit" className="btn btn-primary" disabled={creating}>{creating ? "Adding…" : "Add"}</button>
          </div>
          <div className="f-hint">Recurring monitors run their first scan within a minute, then on the chosen schedule.</div>
        </form>
      )}

      {/* open alerts strip — grouped by monitor, duplicate alerts collapsed to ×N */}
      {alerts.length > 0 && (
        <div className="d-panel mon-alerts">
          <div className="d-panel-h"><Bell size={14} style={{ color: "var(--bad)" }} /> Open alerts <span className="sub">{alerts.length} shown</span></div>
          {groupAlerts(alerts, monitors).map((grp) => (
            <div key={grp.monitorId} className="mon-alert-group">
              <div className="mon-alert-monitor">{grp.name}</div>
              {grp.items.map((a) => (
                <div key={a.id} className="mon-alert">
                  <span className="mon-alert-dot" style={{ background: SEV_COLOR[a.severity] }} />
                  <div className="mon-alert-body">
                    <div className="mon-alert-title">
                      {a.title}
                      {a.count > 1 && <span className="mon-alert-count-badge" title={`${a.count} occurrences`}>×{a.count}</span>}
                    </div>
                    <div className="mon-alert-msg">{a.message}</div>
                  </div>
                  <span className="d-dim d-mono mon-alert-time">{fmtDate(a.created_at)}</span>
                  {canRun && <button className="d-iconbtn" title={a.count > 1 ? "Acknowledge all" : "Acknowledge"} onClick={() => onAckMerged(a)}><CheckCircle2 size={13} /></button>}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* empty state */}
      {monitors.length === 0 ? (
        <div className="d-panel mon-empty">
          <Radar size={30} />
          <div className="mon-empty-t">No monitors yet</div>
          <div className="mon-empty-s">Create a monitor to track a site's AI visibility over time and get alerted when it changes.</div>
          {canRun && <button className="btn btn-primary" onClick={() => setShowForm(true)}><Plus size={15} /> Create your first monitor</button>}
        </div>
      ) : (
        <>
          {active.length > 0 && <div className="mon-section-h">Active</div>}
          <div className="mon-grid">
            {active.map((m) => (
              <MonitorCard key={m.id} m={m} busy={busyId === m.id} canRun={canRun} canDelete={canDelete}
                           onOpen={() => onOpenMonitor(m.id)} onRun={() => onRun(m.id)} onToggle={() => onToggle(m)} onDelete={() => onDelete(m)} />
            ))}
          </div>
          {paused.length > 0 && <div className="mon-section-h">Paused</div>}
          <div className="mon-grid">
            {paused.map((m) => (
              <MonitorCard key={m.id} m={m} busy={busyId === m.id} canRun={canRun} canDelete={canDelete}
                           onOpen={() => onOpenMonitor(m.id)} onRun={() => onRun(m.id)} onToggle={() => onToggle(m)} onDelete={() => onDelete(m)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* Group open alerts under their monitor, and collapse identical alerts (same
   title+message) within a monitor into one row carrying a ×N count and the full
   list of merged ids (so "Acknowledge" clears every occurrence). Monitors keep the
   order they arrive in `alerts` (already newest-first from the API). */
function groupAlerts(alerts, monitors) {
  const nameById = Object.fromEntries((monitors || []).map((m) => [m.id, m.name || m.domain]));
  const groups = new Map();
  for (const a of alerts) {
    const mid = a.monitor_id || "—";
    if (!groups.has(mid)) groups.set(mid, { monitorId: mid, name: nameById[mid] || "Monitor", byKey: new Map() });
    const g = groups.get(mid);
    const key = `${a.title} ${a.message}`;
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
    <div className="mon-card">
      <div className="mon-card-top" onClick={onOpen} role="button">
        <ScoreRing value={m.latest_score} size={52} stroke={6} />
        <div className="mon-card-id">
          <div className="mon-card-name">
            <span className={`mon-status ${m.status}`} /> {m.name || m.domain}
          </div>
          <div className="mon-card-url">{m.domain}</div>
        </div>
        <ChevronRight size={16} className="d-dim" />
      </div>
      <div className="mon-card-meta">
        <TrendBadge trend={m.trend} />
        <span className="mon-freq">{m.frequency}</span>
        {m.open_alert_count > 0 && <span className="mon-alertcount"><Bell size={11} /> {m.open_alert_count}</span>}
      </div>
      <div className="mon-card-times">
        <span><Clock size={11} /> last {m.last_scan_at ? fmtDate(m.last_scan_at) : "—"}</span>
        <span>next {m.next_scan_at ? fmtDate(m.next_scan_at) : (m.frequency === "manual" ? "manual" : "—")}</span>
      </div>
      <div className="mon-card-actions">
        {/* "Run now" uses a refresh icon so it never collides with the Play icon that
            means "Resume" on a paused card (previously both rendered as Play). */}
        {canRun && <button className="d-iconbtn" disabled={busy} onClick={onRun} title="Run now"><RefreshCw size={13} className={busy ? "spin-slow" : ""} /></button>}
        {canRun && (m.status === "active"
          ? <button className="d-iconbtn" disabled={busy} onClick={onToggle} title="Pause"><Pause size={13} /></button>
          : <button className="d-iconbtn" disabled={busy} onClick={onToggle} title="Resume"><Play size={13} /> Resume</button>)}
        <button className="d-iconbtn" onClick={onOpen} title="Open">Details</button>
        {canDelete && <button className="d-iconbtn danger" disabled={busy} onClick={onDelete} title="Delete"><Trash2 size={13} /></button>}
      </div>
    </div>
  );
}
