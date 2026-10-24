/* Monitor detail — MIGRATED to Spectrum. Data flow, hooks, handlers, chart data-building and
   selection logic are byte-for-byte unchanged; only JSX + class names + chart theming changed.
   `.spectrum-screen`-scoped; self-contained loading/error re-skinned inline. */
import React, { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft, Play, Pause, Trash2, FileText, ArrowUpRight, ArrowDownRight,
  AlertTriangle, CheckCircle2, Bell, Clock, Bot, Wrench, CheckCircle, History, Sparkles,
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
import { Shell, Cell, Ring, Tag, Button, Skeleton } from "../shared/spectrum.jsx";
import "./Monitoring.spectrum.css";

const SIGNAL_LABELS = {
  robots: "robots.txt", sitemap: "Sitemap", metadata: "Metadata", schema: "Schema",
  content: "Content", links: "Links", performance: "Performance",
  accessibility: "Accessibility", freshness: "Freshness", ai_readiness: "AI Extractability",
};
const TREND_SIGNALS = ["schema", "metadata", "performance", "ai_readiness", "robots"];
/* 5th color (was --au-lav-d, decorative — not part of the 4-way semantic mapping) has no
   direct Spectrum equivalent; --sp-indigo is used so the 5-line chart keeps 5 distinct hues
   (--sp-lav-d is already used for the 2nd line's "warning" mapping below). */
const LINE_COLORS = ["var(--sp-primary)", "var(--sp-lav-d)", "var(--sp-violet-d)", "var(--sp-cyan-d)", "var(--sp-indigo)"];
const SEV_COLOR = { critical: "var(--sp-violet-d)", warning: "var(--sp-lav-d)", info: "var(--sp-cyan-d)" };
const spScoreColor = (v) => (v >= 75 ? "var(--sp-blue-d)" : v >= 45 ? "var(--sp-lav-d)" : "var(--sp-violet-d)");
const STATUS_VARIANT = { pass: "healthy", warn: "warning", fail: "needs-work" };

const AXIS = { fill: "var(--sp-muted)", fontSize: 10, fontFamily: "'DM Mono', monospace" };
const TT = {
  contentStyle: { background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12, boxShadow: "var(--sp-sh-s)" },
  labelStyle: { color: "var(--sp-muted)" }, itemStyle: { color: "var(--sp-ink)" },
};
const shortDate = (iso) => { try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };

export default function MonitorDetail({ monitorId, onBack, onOpenReport, onOpenIntelligence }) {
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
    <div className="spectrum-screen"><Shell><Cell solid><div className="sp-card-center" role="alert">
      <div className="sp-ill sp-ill-bad"><AlertTriangle size={26} /></div>
      <div className="sp-card-s" style={{ marginBottom: 20 }}>{error}</div>
      <Button variant="gradient" onClick={load}>Retry</Button>
    </div></Cell></Shell></div>
  );
  if (!detail) return (
    <div className="spectrum-screen"><Shell><div className="sp-stack">
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
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-sd-toolbar">
          <Button variant="ghost" onClick={onBack}><ChevronLeft size={14} /> Back to monitoring</Button>
          <div className="sp-sd-toolbar-r">
            {m.latest_scan_id && <Button variant="ghost" onClick={() => onOpenReport(m.latest_scan_id)}><FileText size={13} /> Latest report</Button>}
            {onOpenIntelligence && <Button variant="ghost" onClick={onOpenIntelligence}><Sparkles size={13} /> Site Intelligence</Button>}
            {canRun && <Button variant="ghost" loading={busy} onClick={doRun}>{!busy && <Play size={13} />} {busy ? "Scanning…" : "Run now"}</Button>}
            {canRun && <Button variant="ghost" disabled={busy} onClick={doToggle}>{m.status === "active" ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Resume</>}</Button>}
            {canDelete && <Button variant="ghost" onClick={doDelete}><Trash2 size={13} /> Delete</Button>}
          </div>
        </div>

        <div className="sp-stack">
          {/* header */}
          <Cell solid className="sp-mon-dhead">
            <span role="img" aria-label={`Score ${m.latest_score ?? "not available"}`}><Ring value={m.latest_score} size={84} stroke={8} /></span>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="sp-mon-dtitle">{m.name || m.domain}</div>
              <div className="sp-mon-durl">{m.url}</div>
              <div className="sp-mon-dmeta">
                <span className={`sp-mon-status ${m.status}`} /> {m.status}
                <span className="sp-mon-freq">{m.frequency}</span>
                {m.latest_score != null && <Tag variant={STATUS_VARIANT[m.latest_score >= 75 ? "pass" : m.latest_score >= 45 ? "warn" : "fail"]}>{m.latest_score >= 75 ? "PASS" : m.latest_score >= 45 ? "WARN" : "FAIL"}</Tag>}
              </div>
            </div>
            <div className="sp-mon-dtimes">
              <div><span className="sp-dim">Last scan</span><br />{m.last_scan_at ? fmtDate(m.last_scan_at) : "—"}</div>
              <div><span className="sp-dim">Next scan</span><br />{m.next_scan_at ? fmtDate(m.next_scan_at) : (m.frequency === "manual" ? "manual" : "—")}</div>
              <div><span className="sp-dim">Scans</span><br />{m.history_count}</div>
            </div>
          </Cell>

          {/* detected changes since previous */}
          {changes && !changes.first_scan && (
            <Cell solid>
              <div className="sp-panel-h">Detected changes <span className="sp-sub">vs previous scan</span></div>
              <div className="sp-mon-change-overall" style={{ color: spScoreColor(changes.overall?.curr) }}>
                {deltaIcon(changes.overall?.delta)} Overall {changes.overall?.prev} → <b>{changes.overall?.curr}</b>
                <span className="sp-dim" style={{ marginLeft: 8 }}>({fmtDelta(changes.overall?.delta)})</span>
              </div>
              <div className="sp-mon-change-cols">
                <div>
                  <div className="sp-mon-change-h up">Improvements</div>
                  {(changes.improvements || []).length ? changes.improvements.map((c) => (
                    <div key={c.id} className="sp-mon-change-row"><ArrowUpRight size={12} style={{ color: "var(--sp-blue-d)" }} /> {c.label} <span className="sp-mono">{fmtDelta(c.delta)}</span></div>
                  )) : <div className="sp-dim" style={{ fontSize: 12.5 }}>None</div>}
                </div>
                <div>
                  <div className="sp-mon-change-h down">Regressions</div>
                  {(changes.regressions || []).length ? changes.regressions.map((c) => (
                    <div key={c.id} className="sp-mon-change-row"><ArrowDownRight size={12} style={{ color: "var(--sp-violet-d)" }} /> {c.label} <span className="sp-mono">{fmtDelta(c.delta)}</span></div>
                  )) : <div className="sp-dim" style={{ fontSize: 12.5 }}>None</div>}
                </div>
              </div>
            </Cell>
          )}

          <ChangeAttribution changes={detail.change_set} />
          <CrawlerAccessPanel data={detail.crawler_access} />
          <ScoreTimeline history={history} selected={selectedScan} onSelect={setSelectedScan} />

          <div className="sp-grid2">
            <Cell solid>
              <div className="sp-panel-h">Category score trends</div>
              {catData.length > 1 ? (
                <div className="sp-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={catData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid stroke="var(--sp-line)" vertical={false} />
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
              <div className="sp-panel-h">Issue count trend</div>
              {issueData.length > 1 ? (
                <div className="sp-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={issueData} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid stroke="var(--sp-line)" vertical={false} />
                      <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
                      <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
                      <Tooltip {...TT} cursor={{ fill: "rgba(20,30,51,.05)" }} />
                      <Bar dataKey="issues" fill="var(--sp-lav-d)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : <ChartEmpty label="Needs at least two scans." />}
            </Cell>
          </div>

          {/* critical alerts timeline */}
          <Cell solid>
            <div className="sp-panel-h"><AlertTriangle size={14} style={{ color: "var(--sp-violet-d)" }} /> Critical alerts timeline</div>
            {criticalAlerts.length === 0 ? <div className="sp-dim" style={{ fontSize: 12.5 }}>No critical alerts. 🎉</div> : (
              <div className="sp-mon-timeline">
                {criticalAlerts.map((a) => (
                  <div key={a.id} className="sp-mon-tl-item">
                    <span className="sp-mon-tl-dot" style={{ background: "var(--sp-violet-d)" }} />
                    <div className="sp-mon-tl-body">
                      <div className="sp-mon-tl-title">{a.title}</div>
                      <div className="sp-mon-tl-time">{fmtDate(a.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Cell>

          {/* alert history */}
          <Cell solid>
            <div className="sp-panel-h"><Bell size={14} /> Alert history <span className="sp-sub">{detail.alerts?.length || 0}</span></div>
            {(detail.alerts || []).length === 0 ? <div className="sp-dim" style={{ fontSize: 12.5 }}>No alerts recorded.</div> : detail.alerts.map((a) => (
              <div key={a.id} className="sp-mon-alert">
                <span className="sp-mon-alert-dot" style={{ background: SEV_COLOR[a.severity] }} />
                <div className="sp-mon-alert-body">
                  <div className="sp-mon-alert-title">{a.title} {a.status === "acknowledged" && <span className="sp-mon-ack">acknowledged</span>}</div>
                  <div className="sp-mon-alert-msg">{a.message}</div>
                </div>
                <span className="sp-mon-alert-time">{fmtDate(a.created_at)}</span>
                {a.status === "open" && canRun && <button className="sp-iconbtn" title="Acknowledge" aria-label={`Acknowledge alert: ${a.title}`} onClick={() => ackAlert(a.id)}><CheckCircle2 size={13} /></button>}
              </div>
            ))}
          </Cell>

          {/* history timeline */}
          <Cell solid>
            <div className="sp-panel-h"><Clock size={14} /> Scan history <span className="sp-sub">{detail.history?.length || 0}</span></div>
            {(detail.history || []).length === 0 ? <div className="sp-dim" style={{ fontSize: 12.5 }}>No scans yet.</div> : (
              <div className="sp-mon-hist">
                {detail.history.map((h) => (
                  <div key={h.id} className="sp-mon-hist-row">
                    <span role="img" aria-label={`Score ${h.overall_score}`}><Ring value={h.overall_score} size={34} /></span>
                    <div className="sp-mon-hist-id">
                      <div className="sp-mono" style={{ fontSize: 12.5, color: "var(--sp-ink)" }}>{fmtDate(h.created_at)}</div>
                      <div className="sp-dim" style={{ fontSize: 11 }}>{h.issue_count} issues{h.changes && !h.changes.first_scan && h.changes.overall?.delta != null ? ` · ${fmtDelta(h.changes.overall.delta)}` : " · baseline"}</div>
                    </div>
                    <button className="sp-iconbtn" onClick={() => onOpenReport(h.scan_id)}><FileText size={12} /> Report</button>
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

const CHANGE_SEV_COLOR = { CRITICAL: "var(--sp-violet-d)", WARNING: "var(--sp-lav-d)", INFO: "var(--sp-cyan-d)" };
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
        <div className="sp-panel-h"><History size={14} /> What changed <span className="sp-sub">since the last scan</span></div>
        <div className="sp-dim" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
          <CheckCircle size={12} style={{ color: "var(--sp-blue-d)" }} /> No changes detected since last scan.
        </div>
      </Cell>
    );
  }
  const groups = CHANGE_SEV_ORDER
    .map((sev) => ({ sev, items: changes.filter((c) => c.severity === sev) }))
    .filter((g) => g.items.length);
  return (
    <Cell solid>
      <div className="sp-panel-h"><History size={14} /> What changed <span className="sp-sub">since the last scan</span></div>
      {groups.map((g) => (
        <div key={g.sev} className="sp-cha-group">
          <div className="sp-cha-sev" style={{ color: CHANGE_SEV_COLOR[g.sev] }}>{g.sev} · {g.items.length}</div>
          {g.items.map((c, i) => (
            <div key={i} className="sp-cha-row">
              <span className="sp-cha-dot" style={{ background: CHANGE_SEV_COLOR[g.sev] }} />
              <span className="sp-cha-label">
                <b>{c.category}</b> · {c.path} <span className="sp-dim">({c.change_type})</span>
              </span>
              <span className="sp-cha-ba">
                {c.change_type === "modified" && (
                  <><span className="sp-cha-old">{fmtChangeVal(c.old_value)}</span> → <span className="sp-cha-new">{fmtChangeVal(c.new_value)}</span></>
                )}
                {c.change_type === "removed" && <span className="sp-cha-old">{fmtChangeVal(c.old_value)}</span>}
                {c.change_type === "added" && <span className="sp-cha-new">{fmtChangeVal(c.new_value)}</span>}
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
        <div className="sp-panel-h">Score history <span className="sp-sub">with change events</span></div>
        <ChartEmpty label="Not enough history yet — needs at least two scans." />
      </Cell>
    );
  }

  const renderDot = ({ cx, cy, payload }) => {
    if (payload.sev == null) return <circle cx={cx} cy={cy} r={2.5} fill="var(--sp-primary)" />;
    const on = selected && selected.scan_id === payload.entry.scan_id;
    return (
      <circle cx={cx} cy={cy} r={on ? 7 : 5} fill={CHANGE_SEV_COLOR[payload.sev]}
              stroke="var(--sp-solid)" strokeWidth={2} style={{ cursor: "pointer" }}
              onClick={() => onSelect(payload.entry)} />
    );
  };

  return (
    <Cell solid>
      <div className="sp-panel-h">Score history <span className="sp-sub">click a marker to see what changed</span></div>
      <div className="sp-chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 10, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--sp-line)" vertical={false} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={AXIS} axisLine={false} tickLine={false} />
            <Tooltip {...TT} />
            <Line type="monotone" dataKey="score" stroke="var(--sp-primary)" strokeWidth={2}
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
    <div className="sp-ts-detail">
      <div className="sp-ts-detail-h">
        <span>Changes on {fmtDate(entry.created_at)}</span>
        <button className="sp-iconbtn" onClick={onClose}>Close</button>
      </div>
      {groups.length === 0 ? (
        <div className="sp-dim" style={{ fontSize: 12.5 }}>No changes recorded for this scan.</div>
      ) : groups.map((g) => (
        <div key={g.sev} className="sp-cha-group">
          <div className="sp-cha-sev" style={{ color: CHANGE_SEV_COLOR[g.sev] }}>{g.sev} · {g.items.length}</div>
          {g.items.map((c, i) => (
            <div key={i} className="sp-cha-row">
              <span className="sp-cha-dot" style={{ background: CHANGE_SEV_COLOR[g.sev] }} />
              <span className="sp-cha-label"><b>{c.category}</b> · {c.path} <span className="sp-dim">({c.change_type})</span></span>
              <span className="sp-cha-ba">
                {c.change_type === "modified" && (
                  <><span className="sp-cha-old">{fmtChangeVal(c.old_value)}</span> → <span className="sp-cha-new">{fmtChangeVal(c.new_value)}</span></>
                )}
                {c.change_type === "removed" && <span className="sp-cha-old">{fmtChangeVal(c.old_value)}</span>}
                {c.change_type === "added" && <span className="sp-cha-new">{fmtChangeVal(c.new_value)}</span>}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// "Not blocked" (never "Allowed"/"Readable") — this only confirms robots.txt and the
// server/WAF didn't refuse the bot's request. It is NOT proof an AI platform has
// actually fetched, ingested, or cited this page — that can't be verified from here.
const CRAWLER_STATUS_LABEL = {
  allowed: "Not blocked",
  blocked_by_robots: "Blocked · robots.txt",
  blocked_by_server: "Blocked · server/WAF",
  error: "Not verified",
};
function crawlerColor(b) {
  if (b.status === "allowed") return "var(--sp-blue-d)";
  if (b.status === "error") return "var(--sp-muted)";
  return b.critical ? "var(--sp-violet-d)" : "var(--sp-lav-d)";
}

function CrawlerAccessPanel({ data }) {
  if (!data) return null;
  const bots = data.bots || [];
  const fixes = (data.findings || []).filter((f) => f.bot && f.remediation);
  return (
    <Cell solid>
      <div className="sp-panel-h">
        <Bot size={14} style={{ color: "var(--sp-primary)" }} /> AI Crawler Access
        <span className="sp-sub">whether known AI crawlers are blocked from fetching this site</span>
      </div>
      {data.site_unreachable ? (
        <div className="sp-dim" style={{ fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
          <AlertTriangle size={12} style={{ color: "var(--sp-lav-d)" }} /> The site was unreachable during the last check — crawler access couldn't be verified.
        </div>
      ) : (
        <div className="sp-ca-list">
          {bots.map((b) => (
            <div key={b.key} className="sp-ca-row">
              <span className="sp-ca-dot" style={{ background: crawlerColor(b) }} />
              <span className="sp-ca-name">
                {b.name}
                {b.critical && <span className="sp-ca-crit">critical</span>}
              </span>
              <span className="sp-ca-prov sp-dim">{b.provider}</span>
              <span className="sp-ca-status" style={{ color: crawlerColor(b) }}>
                {b.status === "allowed" && <CheckCircle size={11} />} {CRAWLER_STATUS_LABEL[b.status] || b.status}
              </span>
            </div>
          ))}
        </div>
      )}
      {fixes.length > 0 && (
        <div className="sp-ca-fixes">
          {fixes.map((f, i) => (
            <div key={i} className="sp-ca-fix">
              <Wrench size={11} style={{ color: "var(--sp-primary)" }} />
              <span><b>{f.bot_name}:</b> {f.remediation}</span>
            </div>
          ))}
        </div>
      )}
      {!data.site_unreachable && (
        <div className="sp-dim" style={{ fontSize: 11.5, marginTop: 10 }}>
          "Not blocked" means robots.txt and your server didn't refuse this bot's
          request — it is not confirmation that an AI platform has actually used this
          page in an answer, which can't be verified from outside that platform.
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
  return d > 0 ? <ArrowUpRight size={14} style={{ color: "var(--sp-blue-d)" }} /> : <ArrowDownRight size={14} style={{ color: "var(--sp-violet-d)" }} />;
}
function fmtDelta(d) { return d == null ? "—" : d > 0 ? `+${d}` : `${d}`; }
function ChartEmpty({ label }) {
  return <div className="sp-chart-empty">{label}</div>;
}
