/* Monitor detail — MIGRATED to Aurora. Data flow, hooks, handlers, chart data-building and
   selection logic are byte-for-byte unchanged; only JSX + class names + chart theming changed.
   `.aurora-screen`-scoped; self-contained loading/error re-skinned inline. */
import React, { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft, Play, Pause, Trash2, FileText, ArrowUpRight, ArrowDownRight,
  AlertTriangle, CheckCircle2, Bell, Clock, Bot, Wrench, CheckCircle, History,
} from "lucide-react";
import {
  Line, LineChart, Bar, BarChart, CartesianGrid, Legend,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  getMonitor, getMonitorHistory, runMonitor, updateMonitor, deleteMonitor,
  acknowledgeAlert, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { Shell, Cell, Ring, Tag, Button, Skeleton } from "./aurora.jsx";
import "./Monitoring.aurora.css";

const SIGNAL_LABELS = {
  robots: "robots.txt", sitemap: "Sitemap", metadata: "Metadata", schema: "Schema",
  content: "Content", links: "Links", performance: "Performance",
  accessibility: "Accessibility", freshness: "Freshness", ai_readiness: "AI Extractability",
};
const TREND_SIGNALS = ["schema", "metadata", "performance", "ai_readiness", "robots"];
const LINE_COLORS = ["var(--au-primary)", "var(--au-lemon-d)", "var(--au-peach-d)", "var(--au-sky-d)", "var(--au-lav-d)"];
const SEV_COLOR = { critical: "var(--au-peach-d)", warning: "var(--au-lemon-d)", info: "var(--au-sky-d)" };
const auScoreColor = (v) => (v >= 75 ? "var(--au-mint-d)" : v >= 45 ? "var(--au-lemon-d)" : "var(--au-peach-d)");
const STATUS_VARIANT = { pass: "ok", warn: "warning", fail: "critical" };

const AXIS = { fill: "var(--au-muted)", fontSize: 10, fontFamily: "'DM Mono', monospace" };
const TT = {
  contentStyle: { background: "var(--au-solid)", border: "1px solid var(--au-line)", borderRadius: 12, fontSize: 12, boxShadow: "var(--au-sh-s)" },
  labelStyle: { color: "var(--au-muted)" }, itemStyle: { color: "var(--au-ink)" },
};
const shortDate = (iso) => { try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };

export default function MonitorDetail({ monitorId, onBack, onOpenReport }) {
  const { hasPermission } = useAuth();
  const canRun = hasPermission("scan:run");
  const canDelete = hasPermission("scan:delete");
  const [detail, setDetail] = useState(null);
  const [history, setHistory] = useState(null);
  const [selectedScan, setSelectedScan] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null); setSelectedScan(null);
    try {
      const [d, h] = await Promise.all([
        getMonitor(monitorId),
        getMonitorHistory(monitorId, { days: 90 }).catch(() => null),
      ]);
      setDetail(d); setHistory(h);
    } catch (e) { setError(e instanceof ScanError ? e.message : "Could not load the monitor."); }
  }, [monitorId]);

  useEffect(() => { load(); }, [load]);

  const doRun = async () => { setBusy(true); try { await runMonitor(monitorId); await load(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const doToggle = async () => { setBusy(true); try { await updateMonitor(monitorId, { status: detail.monitor.status === "active" ? "paused" : "active" }); await load(); } finally { setBusy(false); } };
  const doDelete = async () => { if (!window.confirm("Delete this monitor and its history?")) return; try { await deleteMonitor(monitorId); onBack(); } catch (e) { setError(e.message); } };
  const ackAlert = async (id) => { setDetail((d) => ({ ...d, alerts: d.alerts.map((a) => a.id === id ? { ...a, status: "acknowledged" } : a) })); try { await acknowledgeAlert(id); } catch { /* ignore */ } };

  if (error) return (
    <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center" role="alert">
      <div className="au-ill au-ill-bad"><AlertTriangle size={26} /></div>
      <div className="au-card-s" style={{ marginBottom: 20 }}>{error}</div>
      <Button variant="accent" onClick={load}>Retry</Button>
    </div></Cell></Shell></div>
  );
  if (!detail) return (
    <div className="aurora-screen"><Shell><div className="au-stack">
      <Cell solid><div style={{ display: "grid", gap: 10 }}><Skeleton w="40%" h={22} /><Skeleton w="65%" h={12} /></div></Cell>
      {Array.from({ length: 3 }).map((_, i) => <Cell key={i} solid><Skeleton h={40} /></Cell>)}
    </div></Shell></div>
  );

  const m = detail.monitor;
  const trends = detail.trends || {};
  const issueData = (trends.issue_series || []).map((p) => ({ date: shortDate(p.t), issues: p.issues }));
  const catSeries = trends.category_series || {};
  const catData = buildCategoryData(catSeries);
  const changes = detail.latest_changes;
  const criticalAlerts = (detail.alerts || []).filter((a) => a.severity === "critical");

  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to monitoring</Button>
          <div className="au-sd-toolbar-r">
            {m.latest_scan_id && <Button variant="ghost" onClick={() => onOpenReport(m.latest_scan_id)}><FileText size={13} /> Latest report</Button>}
            {canRun && <Button variant="ghost" loading={busy} onClick={doRun}>{!busy && <Play size={13} />} {busy ? "Scanning…" : "Run now"}</Button>}
            {canRun && <Button variant="ghost" disabled={busy} onClick={doToggle}>{m.status === "active" ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Resume</>}</Button>}
            {canDelete && <Button variant="ghost" onClick={doDelete}><Trash2 size={13} /> Delete</Button>}
          </div>
        </div>

        <div className="au-stack">
          {/* header */}
          <Cell solid className="au-mon-dhead">
            <span role="img" aria-label={`Score ${m.latest_score ?? "not available"}`}><Ring value={m.latest_score} size={84} stroke={8} /></span>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="au-mon-dtitle">{m.name || m.domain}</div>
              <div className="au-mon-durl">{m.url}</div>
              <div className="au-mon-dmeta">
                <span className={`au-mon-status ${m.status}`} /> {m.status}
                <span className="au-mon-freq">{m.frequency}</span>
                {m.latest_score != null && <Tag variant={STATUS_VARIANT[m.latest_score >= 75 ? "pass" : m.latest_score >= 45 ? "warn" : "fail"]}>{m.latest_score >= 75 ? "PASS" : m.latest_score >= 45 ? "WARN" : "FAIL"}</Tag>}
              </div>
            </div>
            <div className="au-mon-dtimes">
              <div><span className="au-dim">Last scan</span><br />{m.last_scan_at ? fmtDate(m.last_scan_at) : "—"}</div>
              <div><span className="au-dim">Next scan</span><br />{m.next_scan_at ? fmtDate(m.next_scan_at) : (m.frequency === "manual" ? "manual" : "—")}</div>
              <div><span className="au-dim">Scans</span><br />{m.history_count}</div>
            </div>
          </Cell>

          {/* detected changes since previous */}
          {changes && !changes.first_scan && (
            <Cell solid>
              <div className="au-panel-h">Detected changes <span className="au-sub">vs previous scan</span></div>
              <div className="au-mon-change-overall" style={{ color: auScoreColor(changes.overall?.curr) }}>
                {deltaIcon(changes.overall?.delta)} Overall {changes.overall?.prev} → <b>{changes.overall?.curr}</b>
                <span className="au-dim" style={{ marginLeft: 8 }}>({fmtDelta(changes.overall?.delta)})</span>
              </div>
              <div className="au-mon-change-cols">
                <div>
                  <div className="au-mon-change-h up">Improvements</div>
                  {(changes.improvements || []).length ? changes.improvements.map((c) => (
                    <div key={c.id} className="au-mon-change-row"><ArrowUpRight size={12} style={{ color: "var(--au-mint-d)" }} /> {c.label} <span className="au-mono">{fmtDelta(c.delta)}</span></div>
                  )) : <div className="au-dim" style={{ fontSize: 12.5 }}>None</div>}
                </div>
                <div>
                  <div className="au-mon-change-h down">Regressions</div>
                  {(changes.regressions || []).length ? changes.regressions.map((c) => (
                    <div key={c.id} className="au-mon-change-row"><ArrowDownRight size={12} style={{ color: "var(--au-peach-d)" }} /> {c.label} <span className="au-mono">{fmtDelta(c.delta)}</span></div>
                  )) : <div className="au-dim" style={{ fontSize: 12.5 }}>None</div>}
                </div>
              </div>
            </Cell>
          )}

          <ChangeAttribution changes={detail.change_set} />
          <CrawlerAccessPanel data={detail.crawler_access} />
          <ScoreTimeline history={history} selected={selectedScan} onSelect={setSelectedScan} />

          <div className="au-grid2">
            <Cell solid>
              <div className="au-panel-h">Category score trends</div>
              {catData.length > 1 ? (
                <div className="au-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={catData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid stroke="var(--au-line)" vertical={false} />
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
            </Cell>
            <Cell solid>
              <div className="au-panel-h">Issue count trend</div>
              {issueData.length > 1 ? (
                <div className="au-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={issueData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid stroke="var(--au-line)" vertical={false} />
                      <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
                      <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
                      <Tooltip {...TT} cursor={{ fill: "rgba(20,30,51,.05)" }} />
                      <Bar dataKey="issues" fill="var(--au-lemon-d)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : <ChartEmpty label="Needs at least two scans." />}
            </Cell>
          </div>

          {/* critical alerts timeline */}
          <Cell solid>
            <div className="au-panel-h"><AlertTriangle size={14} style={{ color: "var(--au-peach-d)" }} /> Critical alerts timeline</div>
            {criticalAlerts.length === 0 ? <div className="au-dim" style={{ fontSize: 12.5 }}>No critical alerts. 🎉</div> : (
              <div className="au-mon-timeline">
                {criticalAlerts.map((a) => (
                  <div key={a.id} className="au-mon-tl-item">
                    <span className="au-mon-tl-dot" style={{ background: "var(--au-peach-d)" }} />
                    <div className="au-mon-tl-body">
                      <div className="au-mon-tl-title">{a.title}</div>
                      <div className="au-mon-tl-time">{fmtDate(a.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Cell>

          {/* alert history */}
          <Cell solid>
            <div className="au-panel-h"><Bell size={14} /> Alert history <span className="au-sub">{detail.alerts?.length || 0}</span></div>
            {(detail.alerts || []).length === 0 ? <div className="au-dim" style={{ fontSize: 12.5 }}>No alerts recorded.</div> : detail.alerts.map((a) => (
              <div key={a.id} className="au-mon-alert">
                <span className="au-mon-alert-dot" style={{ background: SEV_COLOR[a.severity] }} />
                <div className="au-mon-alert-body">
                  <div className="au-mon-alert-title">{a.title} {a.status === "acknowledged" && <span className="au-mon-ack">acknowledged</span>}</div>
                  <div className="au-mon-alert-msg">{a.message}</div>
                </div>
                <span className="au-mon-alert-time">{fmtDate(a.created_at)}</span>
                {a.status === "open" && canRun && <button className="au-iconbtn" title="Acknowledge" onClick={() => ackAlert(a.id)}><CheckCircle2 size={13} /></button>}
              </div>
            ))}
          </Cell>

          {/* history timeline */}
          <Cell solid>
            <div className="au-panel-h"><Clock size={14} /> Scan history <span className="au-sub">{detail.history?.length || 0}</span></div>
            {(detail.history || []).length === 0 ? <div className="au-dim" style={{ fontSize: 12.5 }}>No scans yet.</div> : (
              <div className="au-mon-hist">
                {detail.history.map((h) => (
                  <div key={h.id} className="au-mon-hist-row">
                    <span role="img" aria-label={`Score ${h.overall_score}`}><Ring value={h.overall_score} size={34} /></span>
                    <div className="au-mon-hist-id">
                      <div className="au-mono" style={{ fontSize: 12.5, color: "var(--au-ink)" }}>{fmtDate(h.created_at)}</div>
                      <div className="au-dim" style={{ fontSize: 11 }}>{h.issue_count} issues{h.changes && !h.changes.first_scan && h.changes.overall?.delta != null ? ` · ${fmtDelta(h.changes.overall.delta)}` : " · baseline"}</div>
                    </div>
                    <button className="au-iconbtn" onClick={() => onOpenReport(h.scan_id)}><FileText size={12} /> Report</button>
                  </div>
                ))}
              </div>
            )}
          </Cell>
        </div>
      </Shell>
    </div>
  );
}

const CHANGE_SEV_COLOR = { CRITICAL: "var(--au-peach-d)", WARNING: "var(--au-lemon-d)", INFO: "var(--au-sky-d)" };
const CHANGE_SEV_ORDER = ["CRITICAL", "WARNING", "INFO"];

function fmtChangeVal(v) {
  if (v === null || v === undefined) return "—";
  const s = String(v);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}

function ChangeAttribution({ changes }) {
  if (!changes) return null;
  if (changes.length === 0) {
    return (
      <Cell solid>
        <div className="au-panel-h"><History size={14} /> What changed <span className="au-sub">since the last scan</span></div>
        <div className="au-dim" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
          <CheckCircle size={12} style={{ color: "var(--au-mint-d)" }} /> No changes detected since last scan.
        </div>
      </Cell>
    );
  }
  const groups = CHANGE_SEV_ORDER
    .map((sev) => ({ sev, items: changes.filter((c) => c.severity === sev) }))
    .filter((g) => g.items.length);
  return (
    <Cell solid>
      <div className="au-panel-h"><History size={14} /> What changed <span className="au-sub">since the last scan</span></div>
      {groups.map((g) => (
        <div key={g.sev} className="au-cha-group">
          <div className="au-cha-sev" style={{ color: CHANGE_SEV_COLOR[g.sev] }}>{g.sev} · {g.items.length}</div>
          {g.items.map((c, i) => (
            <div key={i} className="au-cha-row">
              <span className="au-cha-dot" style={{ background: CHANGE_SEV_COLOR[g.sev] }} />
              <span className="au-cha-label">
                <b>{c.category}</b> · {c.path} <span className="au-dim">({c.change_type})</span>
              </span>
              <span className="au-cha-ba">
                {c.change_type === "modified" && (
                  <><span className="au-cha-old">{fmtChangeVal(c.old_value)}</span> → <span className="au-cha-new">{fmtChangeVal(c.new_value)}</span></>
                )}
                {c.change_type === "removed" && <span className="au-cha-old">{fmtChangeVal(c.old_value)}</span>}
                {c.change_type === "added" && <span className="au-cha-new">{fmtChangeVal(c.new_value)}</span>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </Cell>
  );
}

function highestSeverity(summary) {
  if (!summary) return null;
  for (const sev of CHANGE_SEV_ORDER) if ((summary[sev] || 0) > 0) return sev;
  return null;
}

function ScoreTimeline({ history, selected, onSelect }) {
  const entries = history?.history;
  if (!entries) return null;
  const points = entries.slice().reverse().map((h) => ({
    date: shortDate(h.created_at), score: h.overall_score,
    sev: highestSeverity(h.change_summary), entry: h,
  }));

  if (points.length < 2) {
    return (
      <Cell solid>
        <div className="au-panel-h">Score history <span className="au-sub">with change events</span></div>
        <ChartEmpty label="Not enough history yet — needs at least two scans." />
      </Cell>
    );
  }

  const renderDot = ({ cx, cy, payload }) => {
    if (payload.sev == null) return <circle cx={cx} cy={cy} r={2.5} fill="var(--au-primary)" />;
    const on = selected && selected.scan_id === payload.entry.scan_id;
    return (
      <circle cx={cx} cy={cy} r={on ? 7 : 5} fill={CHANGE_SEV_COLOR[payload.sev]}
              stroke="var(--au-solid)" strokeWidth={2} style={{ cursor: "pointer" }}
              onClick={() => onSelect(payload.entry)} />
    );
  };

  return (
    <Cell solid>
      <div className="au-panel-h">Score history <span className="au-sub">click a marker to see what changed</span></div>
      <div className="au-chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 10, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--au-line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip {...TT} />
            <Line type="monotone" dataKey="score" stroke="var(--au-primary)" strokeWidth={2}
                  dot={renderDot} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {selected && <SelectedScanChanges entry={selected} onClose={() => onSelect(null)} />}
    </Cell>
  );
}

function SelectedScanChanges({ entry, onClose }) {
  const changes = entry.change_set || [];
  const groups = CHANGE_SEV_ORDER
    .map((sev) => ({ sev, items: changes.filter((c) => c.severity === sev) }))
    .filter((g) => g.items.length);
  return (
    <div className="au-ts-detail">
      <div className="au-ts-detail-h">
        <span>Changes on {fmtDate(entry.created_at)}</span>
        <button className="au-iconbtn" onClick={onClose}>Close</button>
      </div>
      {groups.length === 0 ? (
        <div className="au-dim" style={{ fontSize: 12.5 }}>No changes recorded for this scan.</div>
      ) : groups.map((g) => (
        <div key={g.sev} className="au-cha-group">
          <div className="au-cha-sev" style={{ color: CHANGE_SEV_COLOR[g.sev] }}>{g.sev} · {g.items.length}</div>
          {g.items.map((c, i) => (
            <div key={i} className="au-cha-row">
              <span className="au-cha-dot" style={{ background: CHANGE_SEV_COLOR[g.sev] }} />
              <span className="au-cha-label"><b>{c.category}</b> · {c.path} <span className="au-dim">({c.change_type})</span></span>
              <span className="au-cha-ba">
                {c.change_type === "modified" && (
                  <><span className="au-cha-old">{fmtChangeVal(c.old_value)}</span> → <span className="au-cha-new">{fmtChangeVal(c.new_value)}</span></>
                )}
                {c.change_type === "removed" && <span className="au-cha-old">{fmtChangeVal(c.old_value)}</span>}
                {c.change_type === "added" && <span className="au-cha-new">{fmtChangeVal(c.new_value)}</span>}
              </span>
            </div>
          ))}
        </div>
      ))}
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
  if (b.status === "allowed") return "var(--au-mint-d)";
  if (b.status === "error") return "var(--au-muted)";
  return b.critical ? "var(--au-peach-d)" : "var(--au-lemon-d)";
}

function CrawlerAccessPanel({ data }) {
  if (!data) return null;
  const bots = data.bots || [];
  const fixes = (data.findings || []).filter((f) => f.bot && f.remediation);
  return (
    <Cell solid>
      <div className="au-panel-h">
        <Bot size={14} style={{ color: "var(--au-primary)" }} /> AI Crawler Access
        <span className="au-sub">can the AI crawlers you need actually read this site?</span>
      </div>
      {data.site_unreachable ? (
        <div className="au-dim" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={12} style={{ color: "var(--au-lemon-d)" }} /> The site was unreachable during the last check — crawler access couldn't be verified.
        </div>
      ) : (
        <div className="au-ca-list">
          {bots.map((b) => (
            <div key={b.key} className="au-ca-row">
              <span className="au-ca-dot" style={{ background: crawlerColor(b) }} />
              <span className="au-ca-name">
                {b.name}
                {b.critical && <span className="au-ca-crit">critical</span>}
              </span>
              <span className="au-ca-prov au-dim">{b.provider}</span>
              <span className="au-ca-status" style={{ color: crawlerColor(b) }}>
                {b.status === "allowed" && <CheckCircle size={11} />} {CRAWLER_STATUS_LABEL[b.status] || b.status}
              </span>
            </div>
          ))}
        </div>
      )}
      {fixes.length > 0 && (
        <div className="au-ca-fixes">
          {fixes.map((f, i) => (
            <div key={i} className="au-ca-fix">
              <Wrench size={11} style={{ color: "var(--au-primary)" }} />
              <span><b>{f.bot_name}:</b> {f.remediation}</span>
            </div>
          ))}
        </div>
      )}
    </Cell>
  );
}

function buildCategoryData(catSeries) {
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
  return d > 0 ? <ArrowUpRight size={14} style={{ color: "var(--au-mint-d)" }} /> : <ArrowDownRight size={14} style={{ color: "var(--au-peach-d)" }} />;
}
function fmtDelta(d) { return d == null ? "—" : d > 0 ? `+${d}` : `${d}`; }
function ChartEmpty({ label }) {
  return <div className="au-chart-empty">{label}</div>;
}
