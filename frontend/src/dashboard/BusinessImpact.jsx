/* AI Business Impact — GA4-independent first-party analytics dashboard. Answers,
   for a non-technical business owner: how much traffic comes from AI assistants,
   how it compares to other channels, and whether it's generating real purchases,
   leads, bookings and revenue. A SEPARATE data source from the GA4 AI Traffic page
   (AiTraffic.jsx) — both are shown with a clear source label, never blended into
   one number (see routes_business_impact.py's module docstring on why). */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart3, Check, Copy, Info, Lock, RefreshCw, TrendingUp, Users,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart as RBarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  generateTrackingApiKey, getBusinessImpactChannels, getBusinessImpactLandingPages,
  getBusinessImpactSummary, getBusinessImpactTrafficOverTime, getTrackingStatus,
  listMonitors, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import "./BusinessImpact.spectrum.css";

const CHANNEL_LABELS = {
  chatgpt: "ChatGPT", perplexity: "Perplexity", gemini: "Gemini", copilot: "Copilot",
  claude: "Claude", other_identifiable_ai: "Other AI", organic_search: "Organic search",
  direct: "Direct", paid: "Paid", social: "Social", email: "Email", referral: "Referral",
  unknown: "Unknown",
};
const AI_CHANNELS = ["chatgpt", "perplexity", "gemini", "copilot", "claude", "other_identifiable_ai"];
// Spectrum tokens only (no red/amber/green) — the 4 named AI engines match the fixed
// per-engine colors used everywhere else (SPECTRUM_ENGINES in shared/spectrum.jsx).
const CHANNEL_COLORS = {
  chatgpt: "var(--sp-cyan-d)", perplexity: "var(--sp-blue)", gemini: "var(--sp-indigo)", copilot: "var(--sp-cyan)",
  claude: "var(--sp-violet)", other_identifiable_ai: "var(--sp-violet-d)",
  organic_search: "var(--sp-blue-d)", direct: "var(--sp-muted)", paid: "var(--sp-indigo-d)", social: "var(--sp-lav-d)",
  email: "var(--sp-lav)", referral: "var(--sp-cyan-d)", unknown: "var(--sp-muted)",
};
const RANGES = [
  { label: "Last 7 days", days: 7 }, { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

function money(cents) {
  if (cents == null) return null;
  return (cents / 100).toLocaleString(undefined, { style: "currency", currency: "USD" });
}
function isoDaysAgo(days) {
  const d = new Date(); d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
function todayIso() { return new Date().toISOString().slice(0, 10); }

export default function BusinessImpact() {
  const { openUpgrade } = useUpgrade();
  const [monitors, setMonitors] = useState(null);
  const [monitorId, setMonitorId] = useState(null);
  const [rangeDays, setRangeDays] = useState(30);
  const [tracking, setTracking] = useState(null);
  const [summary, setSummary] = useState(null);
  const [series, setSeries] = useState(null);
  const [channels, setChannels] = useState(null);
  const [pages, setPages] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    listMonitors().then((d) => {
      const list = d.monitors || [];
      setMonitors(list);
      if (list.length) setMonitorId(list[0].id);
      else setLoading(false);
    }).catch((e) => {
      setError(e instanceof ScanError ? e.message : "Could not load your sites.");
      setLoading(false);
    });
  }, []);

  const params = useMemo(() => (monitorId ? {
    monitorId, startDate: isoDaysAgo(rangeDays), endDate: todayIso(),
  } : null), [monitorId, rangeDays]);

  const load = useCallback(async () => {
    if (!params) return;
    setLoading(true); setError(null);
    try {
      const status = await getTrackingStatus(monitorId);
      setTracking(status);
      if (!status.installed) { setLoading(false); return; }
      const s = await getBusinessImpactSummary(params);
      setSummary(s);
      const [t, ch, lp] = await Promise.all([
        getBusinessImpactTrafficOverTime(params),
        s.is_pro ? getBusinessImpactChannels(params) : Promise.resolve(null),
        s.is_pro ? getBusinessImpactLandingPages(params) : Promise.resolve(null),
      ]);
      setSeries(t.series); setChannels(ch?.channels || null); setPages(lp?.pages || null);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load AI Business Impact data.");
    } finally { setLoading(false); }
  }, [params, monitorId]);

  useEffect(() => { load(); }, [load]);

  if (monitors === null) {
    return <Shell className="sp-bi"><Skeleton h={200} /></Shell>;
  }

  if (!monitors.length) {
    return (
      <Shell className="sp-bi">
        <Header />
        <Cell className="sp-bi-card">
          <p>Add a site under Monitoring first — AI Business Impact tracks traffic for a specific site.</p>
        </Cell>
      </Shell>
    );
  }

  return (
    <Shell className="sp-bi">
      <Header />

      {summary?.ga4_connected && (
        <p className="sp-bi-ga4-note">
          This page is AEOMirror's own first-party tracking, kept separate from your connected
          Google Analytics — see <Link to="/app/analytics/ai-traffic">AI Traffic</Link> for the GA4 view.
          The two use different definitions and will not match exactly.
        </p>
      )}

      <div className="sp-bi-toolbar">
        <select className="sp-bi-select" value={monitorId || ""} onChange={(e) => setMonitorId(e.target.value)}>
          {monitors.map((m) => <option key={m.id} value={m.id}>{m.name || m.normalized_url}</option>)}
        </select>
        <div className="sp-bi-ranges">
          {RANGES.map((r) => (
            <button key={r.days} className={rangeDays === r.days ? "on" : ""} onClick={() => setRangeDays(r.days)}>
              {r.label}
            </button>
          ))}
        </div>
        {!loading && (
          <span className="sp-bi-updated">
            {tracking?.last_event_at
              ? `Last event received: ${fmtDate(tracking.last_event_at)}`
              : "Last event received: none yet"}
          </span>
        )}
      </div>

      {error && <div className="sp-bi-banner err">{error}</div>}

      {loading && <Skeleton h={200} />}

      {!loading && tracking && !tracking.installed && (
        <SetupWizard monitorId={monitorId} tracking={tracking} onInstalled={load} />
      )}

      {!loading && tracking?.installed && summary && (
        <>
          {tracking.status === "installed_inactive" && (
            <Cell className="sp-bi-card">
              <Info size={16} />
              <p>No new visits recorded in over a week. This usually just means low traffic —
                but if that's unexpected, confirm the snippet is still present on the live site.</p>
            </Cell>
          )}
          {!summary.has_data ? (
            <Cell className="sp-bi-card">
              <Info size={16} />
              <p>Tracking is installed, but no visits have been recorded yet for this date range.
                This is a real "no data yet" state, not a measurement error — check back once the
                site has had some traffic, or widen the date range.</p>
            </Cell>
          ) : (
            <SummaryCards summary={summary} onUpgrade={() => openUpgrade("business_impact")} />
          )}

          {summary.has_data && series && (
            series.length > 0
              ? <TrafficChart series={series} />
              : <ChartEmptyNote label="Not enough daily traffic yet to chart a trend for this date range." />
          )}

          {summary.is_pro && summary.has_data && channels && (
            channels.length > 0
              ? <ChannelBreakdown channels={channels} />
              : <ChartEmptyNote label="No traffic-source breakdown yet for this date range." />
          )}

          {summary.is_pro && summary.has_data && pages && (
            pages.length > 0
              ? <LandingPageTable pages={pages} />
              : <ChartEmptyNote label="Not enough landing-page data yet for this date range." />
          )}
        </>
      )}
    </Shell>
  );
}

function Header() {
  return (
    <header className="sp-bi-head">
      <h1><BarChart3 size={20} /> AI Business Impact</h1>
      <p className="sp-bi-sub">
        How much of your traffic, leads, bookings and revenue comes from AI assistants
        like ChatGPT, Perplexity, Gemini, Copilot and Claude — measured by AEOMirror's
        own tracking, independent of Google Analytics.
      </p>
    </header>
  );
}

function SetupWizard({ monitorId, tracking, onInstalled }) {
  const [copied, setCopied] = useState(false);
  const [apiKey, setApiKey] = useState(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);

  const copy = async () => {
    try { await navigator.clipboard.writeText(tracking.snippet); setCopied(true); setTimeout(() => setCopied(false), 1800); }
    catch { /* clipboard unavailable — the snippet is still selectable text */ }
  };
  const makeKey = async () => {
    setBusy(true);
    try { setApiKey((await generateTrackingApiKey(monitorId)).api_key); } finally { setBusy(false); }
  };
  const checkNow = async () => { setChecking(true); try { await onInstalled(); } finally { setChecking(false); } };

  return (
    <Cell className="sp-bi-card sp-bi-setup">
      <h2>Install AI Business Impact tracking</h2>
      <p>Paste this one line into your site's HTML, ideally right before <code>&lt;/head&gt;</code> on
        every page. It works on any website — WordPress, Shopify, Webflow, or a custom site.</p>
      <div className="sp-bi-snippet">
        <code>{tracking.snippet}</code>
        <button onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "Copied" : "Copy"}</button>
      </div>
      <ul className="sp-bi-setup-explain">
        <li><strong>Before installing:</strong> you can still use every AEO audit, Question Bank
          and visibility score — tracking installation never blocks those.</li>
        <li><strong>After installing:</strong> this page will show real sessions, AI-source
          breakdown, and (on Pro) confirmed conversions and revenue, as soon as a visitor loads
          a page with the snippet.</li>
      </ul>
      <Button variant="ghost" onClick={checkNow} loading={checking}>
        <RefreshCw size={14} /> Check installation status
      </Button>

      <div className="sp-bi-setup-divider" />
      <h3>Optional: verify real purchases and bookings</h3>
      <p>Visitor-reported conversions are shown as unverified. To count a purchase or booking as
        confirmed, your own backend calls one endpoint after a transaction actually succeeds —
        generate a key for that below.</p>
      {tracking.has_tracking_api_key && !apiKey ? (
        <p className="sp-bi-setup-note">A tracking API key is already configured for this site.</p>
      ) : (
        <Button variant="ghost" onClick={makeKey} loading={busy}>Generate tracking API key</Button>
      )}
      {apiKey && (
        <div className="sp-bi-apikey">
          <code>{apiKey}</code>
          <p>Store this now — it will not be shown again. POST it as <code>Authorization: Bearer …</code> to{" "}
            <code>{tracking.collect_url.replace("/t/collect", `/monitors/${monitorId}/tracking/confirm`)}</code>.</p>
        </div>
      )}
    </Cell>
  );
}

function SummaryCards({ summary, onUpgrade }) {
  const cards = [
    { key: "unique_visitors", label: "Unique visitors", value: summary.unique_visitors?.toLocaleString(), tip: "Distinct first-party visitor IDs in this date range — an estimate: a visitor whose browser storage is unavailable is still counted as a session, just not toward this number." },
    { key: "total_page_views", label: "Page views", value: summary.total_page_views?.toLocaleString(), tip: "Every tracked page load, in this date range." },
    { key: "total_sessions", label: "Website sessions", value: summary.total_sessions?.toLocaleString(), tip: "Every visit to a page with the tracking snippet installed, in this date range." },
    { key: "ai_sessions", label: "Sessions from AI", value: summary.ai_sessions?.toLocaleString(), tip: "Sessions whose first touch was a known AI assistant referral (ChatGPT, Perplexity, Gemini, Copilot, Claude, or another identifiable AI source)." },
    { key: "ai_unique_visitors", label: "Visitors from AI", value: summary.ai_unique_visitors?.toLocaleString(), tip: "Distinct visitors whose first touch was a known AI assistant referral." },
    { key: "ai_share_pct", label: "AI traffic share", value: summary.ai_share_pct != null ? `${summary.ai_share_pct}%` : "—", tip: "AI-referred sessions as a share of all sessions." },
    { key: "ai_conversions", label: "Purchases from AI traffic", value: summary.ai_conversions, locked: !summary.is_pro, tip: "Confirmed conversions attributed to an AI-referred session. Only server-confirmed events count — a visitor-reported event alone is never treated as proof." },
    // `locked` is keyed on the account's actual plan, NEVER on the value being
    // null — ai_conversion_rate_pct is a RATIO (ai_conversions / ai_sessions) that
    // the backend also returns null for whenever ai_sessions is 0 (a real "not
    // enough data" case, not a plan restriction). Keying `locked` off the raw value
    // previously showed a Pro account a false "Pro" paywall for this one card
    // whenever it had zero AI-referred sessions in the selected range.
    { key: "ai_conversion_rate_pct", label: "AI conversion rate",
      value: summary.ai_conversion_rate_pct != null ? `${summary.ai_conversion_rate_pct}%` : "Not enough data yet",
      locked: !summary.is_pro,
      tip: "Confirmed AI-attributed conversions divided by AI-referred sessions. Shows \"Not enough data yet\" (not 0%) when there were no AI-referred sessions in this range." },
    { key: "ai_revenue_cents", label: "Revenue attributed to AI", value: money(summary.ai_revenue_cents), locked: !summary.is_pro, tip: "Confirmed purchase revenue from AI-referred sessions only — never an estimate." },
    { key: "confirmed_bookings", label: "Confirmed bookings", value: summary.confirmed_bookings, locked: !summary.is_pro, tip: "Bookings confirmed via the server-to-server tracking API — never inferred from a page visit.",
      sub: summary.unverified_bookings > 0 ? `+${summary.unverified_bookings} unverified (visitor-reported, not yet confirmed)` : null },
    { key: "leads", label: "Leads generated", value: summary.leads, locked: !summary.is_pro, tip: "Successful lead submissions recorded by the tracker — not merely visits to a contact page.",
      sub: summary.unverified_leads > 0 ? `+${summary.unverified_leads} unverified (visitor-reported, not yet confirmed)` : null },
  ];
  return (
    <div className="sp-bi-cards">
      {cards.map((c) => (
        <Cell key={c.key} className="sp-bi-stat">
          <div className="sp-bi-stat-top">
            <span>{c.label}</span>
            <span className="sp-bi-tip" title={c.tip}><Info size={12} /></span>
          </div>
          {c.locked ? (
            <button className="sp-bi-stat-locked" onClick={onUpgrade}><Lock size={13} /> Pro</button>
          ) : (
            <>
              <div className="sp-bi-stat-v">{c.value ?? "—"}</div>
              {c.sub && <div className="sp-bi-stat-sub">{c.sub}</div>}
            </>
          )}
        </Cell>
      ))}
    </div>
  );
}

function ChartEmptyNote({ label }) {
  return (
    <Cell className="sp-bi-card">
      <Info size={16} />
      <p>{label} This isn't an error — a zero here means no qualifying events were
        recorded, not necessarily that the site received no traffic at all.</p>
    </Cell>
  );
}

function TrafficChart({ series }) {
  const channelsPresent = Array.from(new Set(series.flatMap((d) => Object.keys(d.by_channel))));
  const data = series.map((d) => ({ date: d.date, ...d.by_channel }));
  return (
    <Cell className="sp-bi-card">
      <h2><TrendingUp size={16} /> Traffic over time
        <span className="sp-bi-tip" title="Sessions per day, broken down by traffic source, for the selected date range.">
          <Info size={12} /></span></h2>
      <div className="sp-bi-chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--sp-line)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={(d) => fmtDate(d)} tick={{ fill: "var(--sp-muted)", fontSize: 10 }}
                   axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={{ fill: "var(--sp-muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12 }}
                    labelFormatter={(d) => fmtDate(d)} />
            {channelsPresent.map((ch) => (
              <Area key={ch} type="monotone" dataKey={ch} stackId="1" name={CHANNEL_LABELS[ch] || ch}
                    stroke={CHANNEL_COLORS[ch] || "#999"} fill={CHANNEL_COLORS[ch] || "#999"} fillOpacity={0.5} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="sp-bi-legend">
        {channelsPresent.map((ch) => (
          <span key={ch}><i style={{ background: CHANNEL_COLORS[ch] || "#999" }} />{CHANNEL_LABELS[ch] || ch}</span>
        ))}
      </div>
    </Cell>
  );
}

function ChannelBreakdown({ channels }) {
  const data = channels.map((c) => ({ ...c, label: CHANNEL_LABELS[c.channel] || c.channel }));
  return (
    <Cell className="sp-bi-card">
      <h2><Users size={16} /> Traffic by source
        <span className="sp-bi-tip" title="Sessions grouped by first-touch channel — direct, organic search, social, a named AI assistant, or another identifiable referral.">
          <Info size={12} /></span></h2>
      <div className="sp-bi-chart">
        <ResponsiveContainer width="100%" height="100%">
          <RBarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--sp-line)" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "var(--sp-muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={{ fill: "var(--sp-muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12 }} />
            <Bar dataKey="sessions" fill="var(--sp-primary)" radius={[4, 4, 0, 0]} />
          </RBarChart>
        </ResponsiveContainer>
      </div>
      <table className="sp-bi-table">
        <thead><tr><th>Source</th><th>Sessions</th><th>Conversions</th><th>Conv. rate</th><th>Revenue</th></tr></thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.channel} className={AI_CHANNELS.includes(c.channel) ? "ai" : ""}>
              <td>{c.label}</td><td>{c.sessions.toLocaleString()}</td><td>{c.conversions}</td>
              <td>{c.conversion_rate_pct}%</td><td>{money(c.revenue_cents) || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Cell>
  );
}

const PRIORITY_LABEL = { high: "High priority", medium: "Medium priority", low: "Low priority", unknown: "Insufficient data" };

function LandingPageTable({ pages }) {
  if (!pages.length) return null;
  return (
    <Cell className="sp-bi-card">
      <h2>Pages worth improving</h2>
      <p className="sp-bi-sub-sm">Ranked by observed evidence — real sessions, confirmed conversions, and
        existing AEO issues. Never a guaranteed outcome, and never an estimated revenue gain.</p>
      <div className="sp-bi-pages">
        {pages.map((p) => (
          <div key={p.url} className={`sp-bi-page pri-${p.priority}`}>
            <div className="sp-bi-page-top">
              <a href={p.url} target="_blank" rel="noreferrer">{p.url}</a>
              <span className={`sp-bi-pri pri-${p.priority}`}>{PRIORITY_LABEL[p.priority]}</span>
            </div>
            <div className="sp-bi-page-stats">
              <span>{p.sessions} sessions</span>
              <span>{p.ai_sessions} from AI</span>
              <span>{p.conversions} conversions ({p.conversion_rate_pct}%)</span>
              {p.revenue_cents > 0 && <span>{money(p.revenue_cents)} revenue</span>}
              {p.aeo && <span>{p.aeo.open_issue_count} open AEO issue(s)</span>}
            </div>
            {p.recommendation && <p className="sp-bi-page-rec">{p.recommendation}</p>}
            {p.reason && <p className="sp-bi-page-reason">Why: {p.reason}</p>}
            {p.aeo?.scan_id && (
              <a className="sp-bi-page-link" href={`/app/scans/${p.aeo.scan_id}`}>View this page's AEO report →</a>
            )}
          </div>
        ))}
      </div>
    </Cell>
  );
}
