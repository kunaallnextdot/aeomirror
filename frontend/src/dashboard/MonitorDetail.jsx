/* Monitor detail (Phase 7): historical timeline, charts (score over time, category
   trends, issue count, critical-alert timeline), detected changes, latest report
   link, and alert history. */
import React, { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft, Play, Pause, Trash2, FileText, ArrowUpRight, ArrowDownRight,
  AlertTriangle, CheckCircle2, Bell, Clock, Bot, Wrench, CheckCircle,
} from "lucide-react";
import {
  Area, AreaChart, Line, LineChart, Bar, BarChart, CartesianGrid, Legend,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  getMonitor, runMonitor, updateMonitor, deleteMonitor, acknowledgeAlert, ScanError,
} from "../api.js";
import { ScoreRing, scoreColor, fmtDate, TableSkeleton, ErrorState, StatusBadge } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";

const SIGNAL_LABELS = {
  robots: "robots.txt", sitemap: "Sitemap", metadata: "Metadata", schema: "Schema",
  content: "Content", links: "Links", performance: "Performance",
  accessibility: "Accessibility", freshness: "Freshness", ai_readiness: "AI Extractability",
};
const TREND_SIGNALS = ["schema", "metadata", "performance", "ai_readiness", "robots"];
const LINE_COLORS = ["#34D3E0", "#E6A94A", "#E5615B", "#43C08A", "#8A97A3"];
const SEV_COLOR = { critical: "var(--bad)", warning: "var(--warn)", info: "var(--accent)" };

const AXIS = { fill: "var(--txt-dim)", fontSize: 10 };
const TT = {
  contentStyle: { background: "var(--panel-2)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "var(--txt-mid)" }, itemStyle: { color: "var(--txt)" },
};
const shortDate = (iso) => { try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };

export default function MonitorDetail({ monitorId, onBack, onOpenReport }) {
  const { hasPermission } = useAuth();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try { setDetail(await getMonitor(monitorId)); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not load the monitor."); }
  }, [monitorId]);

  useEffect(() => { load(); }, [load]);

  const doRun = async () => { setBusy(true); try { await runMonitor(monitorId); await load(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const doToggle = async () => { setBusy(true); try { await updateMonitor(monitorId, { status: detail.monitor.status === "active" ? "paused" : "active" }); await load(); } finally { setBusy(false); } };
  const doDelete = async () => { if (!window.confirm("Delete this monitor and its history?")) return; try { await deleteMonitor(monitorId); onBack(); } catch (e) { setError(e.message); } };
  const ackAlert = async (id) => { setDetail((d) => ({ ...d, alerts: d.alerts.map((a) => a.id === id ? { ...a, status: "acknowledged" } : a) })); try { await acknowledgeAlert(id); } catch { /* ignore */ } };

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!detail) return <TableSkeleton rows={5} />;

  const m = detail.monitor;
  const trends = detail.trends || {};
  const scoreData = (trends.score_series || []).map((p) => ({ date: shortDate(p.t), score: p.score }));
  const issueData = (trends.issue_series || []).map((p) => ({ date: shortDate(p.t), issues: p.issues }));
  const catSeries = trends.category_series || {};
  const catData = buildCategoryData(catSeries);
  const changes = detail.latest_changes;
  const criticalAlerts = (detail.alerts || []).filter((a) => a.severity === "critical");

  return (
    <div className="mon-detail">
      <div className="d-toolbar" style={{ justifyContent: "space-between" }}>
        <button className="d-iconbtn" onClick={onBack}><ChevronLeft size={14} /> Back to monitoring</button>
        <div style={{ display: "flex", gap: 8 }}>
          {m.latest_scan_id && <button className="d-iconbtn" onClick={() => onOpenReport(m.latest_scan_id)}><FileText size={13} /> Latest report</button>}
          {canRun && <button className="d-iconbtn" disabled={busy} onClick={doRun}><Play size={13} /> {busy ? "Scanning…" : "Run now"}</button>}
          {canRun && <button className="d-iconbtn" disabled={busy} onClick={doToggle}>{m.status === "active" ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Resume</>}</button>}
          {canDelete && <button className="d-iconbtn danger" onClick={doDelete}><Trash2 size={13} /> Delete</button>}
        </div>
      </div>

      {/* header */}
      <div className="d-panel mon-dhead">
        <ScoreRing value={m.latest_score} size={84} stroke={8} />
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="mon-dtitle">{m.name || m.domain}</div>
          <div className="mon-durl">{m.url}</div>
          <div className="mon-dmeta">
            <span className={`mon-status ${m.status}`} /> {m.status}
            <span className="mon-freq">{m.frequency}</span>
            {m.latest_score != null && <StatusBadge status={m.latest_score >= 75 ? "pass" : m.latest_score >= 45 ? "warn" : "fail"} />}
          </div>
        </div>
        <div className="mon-dtimes">
          <div><span className="d-dim">Last scan</span><br />{m.last_scan_at ? fmtDate(m.last_scan_at) : "—"}</div>
          <div><span className="d-dim">Next scan</span><br />{m.next_scan_at ? fmtDate(m.next_scan_at) : (m.frequency === "manual" ? "manual" : "—")}</div>
          <div><span className="d-dim">Scans</span><br />{m.history_count}</div>
        </div>
      </div>

      {/* detected changes since previous */}
      {changes && !changes.first_scan && (
        <div className="d-panel" style={{ marginTop: 14 }}>
          <div className="d-panel-h">Detected changes <span className="sub">vs previous scan</span></div>
          <div className="mon-change-overall" style={{ color: scoreColor(changes.overall?.curr) }}>
            {deltaIcon(changes.overall?.delta)} Overall {changes.overall?.prev} → <b>{changes.overall?.curr}</b>
            <span className="d-dim" style={{ marginLeft: 8 }}>({fmtDelta(changes.overall?.delta)})</span>
          </div>
          <div className="mon-change-cols">
            <div>
              <div className="mon-change-h up">Improvements</div>
              {(changes.improvements || []).length ? changes.improvements.map((c) => (
                <div key={c.id} className="mon-change-row"><ArrowUpRight size={12} style={{ color: "var(--good)" }} /> {c.label} <span className="d-mono">{fmtDelta(c.delta)}</span></div>
              )) : <div className="d-dim" style={{ fontSize: 12.5 }}>None</div>}
            </div>
            <div>
              <div className="mon-change-h down">Regressions</div>
              {(changes.regressions || []).length ? changes.regressions.map((c) => (
                <div key={c.id} className="mon-change-row"><ArrowDownRight size={12} style={{ color: "var(--bad)" }} /> {c.label} <span className="d-mono">{fmtDelta(c.delta)}</span></div>
              )) : <div className="d-dim" style={{ fontSize: 12.5 }}>None</div>}
            </div>
          </div>
        </div>
      )}

      {/* AI crawler access (from the latest scan) */}
      <CrawlerAccessPanel data={detail.crawler_access} />

      {/* charts */}
      <div className="d-panel" style={{ marginTop: 14 }}>
        <div className="d-panel-h">Score over time</div>
        {scoreData.length > 1 ? (
          <div className="d-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={scoreData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                <defs><linearGradient id="mg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--accent)" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
                <Tooltip {...TT} />
                <Area type="monotone" dataKey="score" stroke="var(--accent)" strokeWidth={2} fill="url(#mg)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : <ChartEmpty label="Needs at least two scans to chart a trend." />}
      </div>

      <div className="d-grid d-grid-2" style={{ marginTop: 14 }}>
        <div className="d-panel">
          <div className="d-panel-h">Category score trends</div>
          {catData.length > 1 ? (
            <div className="d-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={catData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {TREND_SIGNALS.filter((s) => catSeries[s]).map((s, i) => (
                    <Line key={s} type="monotone" dataKey={s} name={SIGNAL_LABELS[s]} stroke={LINE_COLORS[i % LINE_COLORS.length]} strokeWidth={1.6} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : <ChartEmpty label="Needs at least two scans." />}
        </div>
        <div className="d-panel">
          <div className="d-panel-h">Issue count trend</div>
          {issueData.length > 1 ? (
            <div className="d-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={issueData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
                  <Tooltip {...TT} cursor={{ fill: "var(--panel-2)" }} />
                  <Bar dataKey="issues" fill="var(--warn)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <ChartEmpty label="Needs at least two scans." />}
        </div>
      </div>

      {/* critical alerts timeline */}
      <div className="d-panel" style={{ marginTop: 14 }}>
        <div className="d-panel-h"><AlertTriangle size={14} style={{ color: "var(--bad)" }} /> Critical alerts timeline</div>
        {criticalAlerts.length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>No critical alerts. 🎉</div> : (
          <div className="mon-timeline">
            {criticalAlerts.map((a) => (
              <div key={a.id} className="mon-tl-item">
                <span className="mon-tl-dot" style={{ background: "var(--bad)" }} />
                <div className="mon-tl-body">
                  <div className="mon-tl-title">{a.title}</div>
                  <div className="mon-tl-time d-mono d-dim">{fmtDate(a.created_at)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* alert history */}
      <div className="d-panel" style={{ marginTop: 14 }}>
        <div className="d-panel-h"><Bell size={14} /> Alert history <span className="sub">{detail.alerts?.length || 0}</span></div>
        {(detail.alerts || []).length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>No alerts recorded.</div> : detail.alerts.map((a) => (
          <div key={a.id} className="mon-alert">
            <span className="mon-alert-dot" style={{ background: SEV_COLOR[a.severity] }} />
            <div className="mon-alert-body">
              <div className="mon-alert-title">{a.title} {a.status === "acknowledged" && <span className="mon-ack">acknowledged</span>}</div>
              <div className="mon-alert-msg">{a.message}</div>
            </div>
            <span className="d-dim d-mono mon-alert-time">{fmtDate(a.created_at)}</span>
            {a.status === "open" && canRun && <button className="d-iconbtn" title="Acknowledge" onClick={() => ackAlert(a.id)}><CheckCircle2 size={13} /></button>}
          </div>
        ))}
      </div>

      {/* history timeline */}
      <div className="d-panel" style={{ marginTop: 14 }}>
        <div className="d-panel-h"><Clock size={14} /> Scan history <span className="sub">{detail.history?.length || 0}</span></div>
        {(detail.history || []).length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>No scans yet.</div> : (
          <div className="mon-hist">
            {detail.history.map((h) => (
              <div key={h.id} className="mon-hist-row">
                <ScoreRing value={h.overall_score} size={34} />
                <div className="mon-hist-id">
                  <div className="d-mono" style={{ fontSize: 12.5 }}>{fmtDate(h.created_at)}</div>
                  <div className="d-dim" style={{ fontSize: 11 }}>{h.issue_count} issues{h.changes && !h.changes.first_scan && h.changes.overall?.delta != null ? ` · ${fmtDelta(h.changes.overall.delta)}` : " · baseline"}</div>
                </div>
                <button className="d-iconbtn" onClick={() => onOpenReport(h.scan_id)}><FileText size={12} /> Report</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const CRAWLER_STATUS_LABEL = {
  allowed: "Allowed",
  blocked_by_robots: "Blocked · robots.txt",
  blocked_by_server: "Blocked · server/WAF",
  error: "Not verified",
};
function crawlerColor(b) {
  if (b.status === "allowed") return "var(--good)";
  if (b.status === "error") return "var(--txt-mid)";
  return b.critical ? "var(--bad)" : "var(--warn)";   // a block
}

function CrawlerAccessPanel({ data }) {
  if (!data) return null;   // null for old scans / when the check is disabled
  const bots = data.bots || [];
  const fixes = (data.findings || []).filter((f) => f.bot && f.remediation);
  return (
    <div className="d-panel" style={{ marginTop: 14 }}>
      <div className="d-panel-h">
        <Bot size={14} style={{ color: "var(--accent)" }} /> AI Crawler Access
        <span className="sub">can the AI crawlers you need actually read this site?</span>
      </div>
      {data.site_unreachable ? (
        <div className="d-dim" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={12} style={{ color: "var(--warn)" }} /> The site was unreachable during the last check — crawler access couldn't be verified.
        </div>
      ) : (
        <div className="ca-list">
          {bots.map((b) => (
            <div key={b.key} className="ca-row">
              <span className="ca-dot" style={{ background: crawlerColor(b) }} />
              <span className="ca-name">
                {b.name}
                {b.critical && <span className="ca-crit">critical</span>}
              </span>
              <span className="ca-prov d-dim">{b.provider}</span>
              <span className="ca-status" style={{ color: crawlerColor(b) }}>
                {b.status === "allowed" && <CheckCircle size={11} />} {CRAWLER_STATUS_LABEL[b.status] || b.status}
              </span>
            </div>
          ))}
        </div>
      )}
      {fixes.length > 0 && (
        <div className="ca-fixes">
          {fixes.map((f, i) => (
            <div key={i} className="ca-fix">
              <Wrench size={11} style={{ color: "var(--accent)" }} />
              <span><b>{f.bot_name}:</b> {f.remediation}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function buildCategoryData(catSeries) {
  // Merge per-signal series into rows keyed by timestamp for a multi-line chart.
  const byTime = {};
  for (const sid of Object.keys(catSeries)) {
    for (const p of catSeries[sid]) {
      const key = p.t;
      byTime[key] = byTime[key] || { date: shortDate(p.t), _t: p.t };
      byTime[key][sid] = p.score;
    }
  }
  return Object.values(byTime).sort((a, b) => new Date(a._t) - new Date(b._t));
}

function deltaIcon(d) {
  if (d == null || d === 0) return null;
  return d > 0 ? <ArrowUpRight size={14} style={{ color: "var(--good)" }} /> : <ArrowDownRight size={14} style={{ color: "var(--bad)" }} />;
}
function fmtDelta(d) { return d == null ? "—" : d > 0 ? `+${d}` : `${d}`; }
function ChartEmpty({ label }) {
  return <div className="d-chart" style={{ display: "grid", placeItems: "center", color: "var(--txt-dim)", fontSize: 12.5 }}>{label}</div>;
}
