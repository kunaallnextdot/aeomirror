/* AI Business Impact — Website Intelligence Command Center for ONE website at a time.
   Everything shown comes from a real source, labelled as such:
     · /business-impact/command-center — per-website indicators (technical audit, AI
       answer visibility, first-party traffic, GA4 if explicitly linked, conversion
       measurement, site-crawl findings) and evidence-backed findings
     · /business-impact/{summary,traffic-over-time,channels,landing-pages} — the
       first-party tracker's detail views
   AI *referral traffic* (visits from an AI assistant) and AI *answer visibility*
   (mentions in tracked AI answers) are different measurements and are never merged.
   A missing value is shown as its state ("Not set up", "Out of date"…), never as 0. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity, AlertTriangle, ArrowRight, BarChart3, Check, ChevronDown, ChevronUp, Copy,
  Info, Lock, MessageSquareText, RefreshCw, Search, ShieldCheck, Target, TrendingUp, Users,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart as RBarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  generateTrackingApiKey, getBusinessImpactChannels, getBusinessImpactLandingPages,
  getBusinessImpactSummary, getBusinessImpactTrafficOverTime, getCommandCenter,
  getTrackingStatus, linkGa4Website, listMonitors, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import "./BusinessImpact.spectrum.css";

const CHANNEL_LABELS = {
  chatgpt: "ChatGPT", perplexity: "Perplexity", gemini: "Gemini", copilot: "Copilot",
  claude: "Claude", other_identifiable_ai: "Other AI", organic_search: "Organic search",
  direct: "Direct", paid: "Paid", social: "Social", email: "Email", referral: "Referral",
  unknown: "Unknown", unlinked: "No linked visit",
};
const AI_CHANNELS = ["chatgpt", "perplexity", "gemini", "copilot", "claude", "other_identifiable_ai"];
const CHANNEL_COLORS = {
  chatgpt: "var(--sp-cyan-d)", perplexity: "var(--sp-blue)", gemini: "var(--sp-indigo)", copilot: "var(--sp-cyan)",
  claude: "var(--sp-violet)", other_identifiable_ai: "var(--sp-violet-d)",
  organic_search: "var(--sp-blue-d)", direct: "var(--sp-muted)", paid: "var(--sp-indigo-d)", social: "var(--sp-lav-d)",
  email: "var(--sp-lav)", referral: "var(--sp-cyan-d)", unknown: "var(--sp-muted)",
};
const RANGES = [
  { label: "7 days", days: 7 }, { label: "28 days", days: 28 },
  { label: "90 days", days: 90 }, { label: "Custom", days: "custom" },
];
const STATUS_LABEL = {
  available: "Current", zero: "No activity", unavailable: "Unavailable", not_configured: "Not set up",
  insufficient_data: "Insufficient data", stale: "Out of date", partial: "Partial", error: "Error",
};
const SOURCE_LABEL = {
  aeomirror_scan: "AEOMirror audit", answer_tracking: "Answer Tracking", first_party: "AEOMirror tracking",
  ga4: "Google Analytics 4", url_intelligence: "Site crawl",
};
const CATEGORY_LABEL = {
  technical_seo: "Technical SEO", ai_visibility: "AI answer visibility", ai_referral: "AI referral traffic",
  acquisition_engagement: "Acquisition & engagement", conversion_measurement: "Conversion measurement",
  landing_page: "Landing page",
};
const STATEMENT_LABEL = { observed: "Observed", calculated: "Calculated", hypothesis: "Hypothesis", recommendation: "Next step", unavailable: "Unknown" };

function money(cents) {
  if (cents == null) return null;
  return (cents / 100).toLocaleString(undefined, { style: "currency", currency: "USD" });
}
// "Last N days" = N calendar days ending today (UTC, matching the API's day boundaries).
function isoDaysAgo(days) {
  const d = new Date(); d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}
function todayIso() { return new Date().toISOString().slice(0, 10); }
const fmtNum = (n) => (n == null ? null : Number(n).toLocaleString());
const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
const PROVIDER_LABEL = { openai: "OpenAI", anthropic: "Anthropic", perplexity: "Perplexity", gemini: "Gemini" };
const providers = (list) => (list || []).map((p) => PROVIDER_LABEL[p] || p).join(", ");

export default function BusinessImpact() {
  const { openUpgrade } = useUpgrade();
  const { hasPermission } = useAuth();
  const canManage = hasPermission ? hasPermission("org:update") : false;
  const [search, setSearch] = useSearchParams();
  const [monitors, setMonitors] = useState(null);
  const [rangeDays, setRangeDays] = useState(28);
  const [customStart, setCustomStart] = useState(isoDaysAgo(27));
  const [customEnd, setCustomEnd] = useState(todayIso());
  const [compare, setCompare] = useState(true);
  const [cc, setCc] = useState(null);
  const [tracking, setTracking] = useState(null);
  const [summary, setSummary] = useState(null);
  const [series, setSeries] = useState(null);
  const [channels, setChannels] = useState(null);
  const [pages, setPages] = useState(null);
  const [sectionErrors, setSectionErrors] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const monitorId = search.get("site");
  const setMonitorId = (id) => setSearch((p) => { const n = new URLSearchParams(p); n.set("site", id); return n; }, { replace: true });

  useEffect(() => {
    listMonitors().then((d) => {
      const list = d.monitors || [];
      setMonitors(list);
      // A ?site= that isn't one of this org's websites is ignored, never trusted.
      if (list.length && !list.some((m) => m.id === search.get("site"))) setMonitorId(list[0].id);
      if (!list.length) setLoading(false);
    }).catch((e) => {
      setError(e instanceof ScanError ? e.message : "Could not load your sites.");
      setLoading(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const validSite = monitors?.some((m) => m.id === monitorId);
  const params = useMemo(() => {
    if (!monitorId || !validSite) return null;
    const startDate = rangeDays === "custom" ? customStart : isoDaysAgo(rangeDays - 1);
    const endDate = rangeDays === "custom" ? customEnd : todayIso();
    return { monitorId, startDate, endDate };
  }, [monitorId, validSite, rangeDays, customStart, customEnd]);

  // Each load gets a generation number; a response may update state only while its
  // load is still the latest, so a slow response for the previous website or range
  // can never land on screen under the newly selected one.
  const loadGen = useRef(0);
  const load = useCallback(async () => {
    if (!params) return;
    const gen = ++loadGen.current;
    const live = () => loadGen.current === gen;
    setLoading(true); setError(null); setSectionErrors({});
    // Switching website or range clears everything first — one site's numbers are
    // never left on screen while another site's are loading.
    setCc(null); setSummary(null); setSeries(null); setChannels(null); setPages(null);
    const msg = (r, fallback) => (r.status === "rejected"
      ? (r.reason instanceof ScanError ? r.reason.message : fallback) : null);
    try {
      const [ccRes, trRes] = await Promise.allSettled([getCommandCenter(params), getTrackingStatus(monitorId)]);
      if (!live()) return;
      if (ccRes.status === "fulfilled") setCc(ccRes.value);
      if (trRes.status === "rejected") throw trRes.reason;
      const status = trRes.value;
      setTracking(status);
      const errs = { commandCenter: msg(ccRes, "Could not load this website's intelligence summary.") };
      if (status.installed) {
        const s = await getBusinessImpactSummary(params);
        if (!live()) return;
        setSummary(s);
        const [t, ch, lp] = await Promise.allSettled([
          getBusinessImpactTrafficOverTime(params),
          s.is_pro ? getBusinessImpactChannels(params) : Promise.resolve(null),
          s.is_pro ? getBusinessImpactLandingPages(params) : Promise.resolve(null),
        ]);
        if (!live()) return;
        if (t.status === "fulfilled") setSeries(t.value.series);
        if (ch.status === "fulfilled") setChannels(ch.value?.channels || null);
        if (lp.status === "fulfilled") setPages(lp.value?.pages || null);
        Object.assign(errs, {
          trend: msg(t, "Could not load traffic over time."),
          channels: msg(ch, "Could not load traffic sources."),
          pages: msg(lp, "Could not load landing pages."),
        });
      }
      setSectionErrors(errs);
    } catch (e) {
      if (!live()) return;
      setCc(null); setSummary(null); setSeries(null); setChannels(null); setPages(null);
      setError(e instanceof ScanError ? e.message : "Could not load AI Business Impact data.");
    } finally { if (live()) setLoading(false); }
  }, [params, monitorId]);

  useEffect(() => { load(); }, [load]);

  const linkGa4 = async () => {
    try { await linkGa4Website(monitorId); await load(); }
    catch (e) { setError(e instanceof ScanError ? e.message : "Could not link Google Analytics to this website."); }
  };

  if (monitors === null) return <Shell className="sp-bi"><Skeleton h={200} /></Shell>;

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

  const ind = cc?.indicators;
  const traffic = ind?.traffic;
  const showDeltas = compare && traffic?.previous_comparable;

  return (
    <Shell className="sp-bi">
      <Header />

      <div className="sp-bi-toolbar">
        <select className="sp-bi-select" aria-label="Website" value={monitorId || ""} onChange={(e) => setMonitorId(e.target.value)}>
          {monitors.map((m) => <option key={m.id} value={m.id}>{m.name || m.domain || m.normalized_url}</option>)}
        </select>
        <div className="sp-bi-ranges" role="group" aria-label="Date range">
          {RANGES.map((r) => (
            <button key={r.label} className={rangeDays === r.days ? "on" : ""} aria-pressed={rangeDays === r.days}
                    onClick={() => setRangeDays(r.days)}>{r.label}</button>
          ))}
        </div>
        <label className="sp-bi-compare">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
          Compare to previous period
        </label>
        {!loading && (
          <span className="sp-bi-updated">
            {tracking?.last_event_at
              ? `Last event received: ${fmtDate(tracking.last_event_at)}`
              : "Last event received: none yet"}
          </span>
        )}
      </div>
      {rangeDays === "custom" && (
        <div className="sp-bi-custom">
          <label>From <input type="date" value={customStart} max={customEnd} onChange={(e) => setCustomStart(e.target.value)} /></label>
          <label>To <input type="date" value={customEnd} min={customStart} max={todayIso()} onChange={(e) => setCustomEnd(e.target.value)} /></label>
          <span className="sp-bi-hint">Up to 90 days. Dates are in UTC.</span>
        </div>
      )}

      {error && <div className="sp-bi-banner err">{error}</div>}
      {loading && <Skeleton h={200} />}

      {!loading && !error && (
        <>
          {sectionErrors.commandCenter && <SectionError title="Website overview" message={sectionErrors.commandCenter} />}
          {ind && (
            <>
              <Indicators ind={ind} siteId={monitorId} />
              {ind.ga4?.link_required && canManage && (
                <div className="sp-bi-linkbar">
                  <Info size={14} />
                  <span>Google Analytics property “{ind.ga4.property_name}” isn't linked to a website, so its data
                    isn't shown here. Link it only if this is the site it measures.</span>
                  <Button variant="ghost" onClick={linkGa4}>Link to this website</Button>
                </div>
              )}
              <Findings findings={cc.findings} range={cc.range} />
            </>
          )}

          {tracking && !tracking.installed && (
            <SetupWizard monitorId={monitorId} tracking={tracking} onInstalled={load} />
          )}

          {tracking?.installed && summary && (
            <>
              {tracking.status === "installed_inactive" && (
                <Cell className="sp-bi-card">
                  <Info size={16} />
                  <p>No new visits recorded in over a week. This usually just means low traffic —
                    but if that's unexpected, confirm the snippet is still present on the live site.</p>
                </Cell>
              )}
              {!summary.has_data && (
                <div className="sp-bi-nodata-banner">
                  <Info size={14} />
                  <span>Your tracking is active. No qualifying visits were recorded during this
                    period yet. Try another date range, or check back after your site receives traffic.</span>
                </div>
              )}

              <SectionTitle icon={Activity} title="Website performance" source="AEOMirror tracking"
                            note={summary.ga4_connected ? (
                              <>Measured by AEOMirror's own snippet, kept separate from your Google Analytics —
                                see <Link to="/app/analytics/ai-traffic">AI Traffic</Link> for the GA4 view. The two
                                use different definitions (tags, consent, ad blockers, session rules) and won't match exactly.</>
                            ) : null} />
              <SummaryCards summary={summary} traffic={showDeltas ? traffic : null}
                            onUpgrade={() => openUpgrade("business_impact")} />
              {compare && traffic?.change_note && <p className="sp-bi-change-note">{traffic.change_note}</p>}

              {sectionErrors.trend
                ? <SectionError title="Traffic over time" message={sectionErrors.trend} />
                : series && <TrafficChart series={series} />}

              <AiSearchIntelligence ind={ind} channels={channels} isPro={summary.is_pro} />

              {summary.is_pro && (sectionErrors.channels
                ? <SectionError title="Traffic by source" message={sectionErrors.channels} />
                : channels && <ChannelBreakdown channels={channels} conversionMeasured={summary.conversion_tracking_configured} />)}

              {summary.is_pro && (sectionErrors.pages
                ? <SectionError title="Landing pages" message={sectionErrors.pages} />
                : pages && <LandingPageTable pages={pages} />)}
            </>
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
        One website's condition and next priorities, from your audits, AI answer tracking,
        and measured traffic — each labelled with where it came from.
      </p>
    </header>
  );
}

function SectionTitle({ icon: Icon, title, source, note }) {
  return (
    <div className="sp-bi-section-title">
      <h2>{Icon && <Icon size={16} />} {title}</h2>
      {source && <span className="sp-bi-source">Source: {source}</span>}
      {note && <p className="sp-bi-sub-sm">{note}</p>}
    </div>
  );
}

function SectionError({ title, message }) {
  return (
    <Cell className="sp-bi-card">
      <h2>{title}</h2>
      <p className="sp-bi-section-error"><AlertTriangle size={14} /> {message}</p>
    </Cell>
  );
}

/* ---------------------------------------------------------------- indicators */

function StatusPill({ status }) {
  return <span className={`sp-bi-status st-${status}`}>{STATUS_LABEL[status] || status}</span>;
}

function IndicatorCard({ title, icon: Icon, data, value, detail, linkTo, linkLabel }) {
  const status = data?.status || "unavailable";
  const hasValue = value != null && !["not_configured", "unavailable"].includes(status);
  return (
    <div className="sp-bi-ind">
      <div className="sp-bi-ind-top">
        <span className="sp-bi-ind-title">{Icon && <Icon size={14} />} {title}</span>
        <StatusPill status={status} />
      </div>
      <div className="sp-bi-ind-value">{hasValue ? value : "—"}</div>
      <div className="sp-bi-ind-detail">{hasValue ? detail : (data?.reason || "No data from this source yet.")}</div>
      {/* A shown value can still be qualified (partial history, a failed latest sync). */}
      {hasValue && data?.reason && <div className="sp-bi-ind-note">{data.reason}</div>}
      <div className="sp-bi-ind-meta">
        <span title={data?.provenance ? [data.provenance.query, data.provenance.aggregation].filter(Boolean).join(" — ") : undefined}>
          {SOURCE_LABEL[data?.source] || ""}{data?.as_of ? ` · ${fmtDay(data.as_of)}` : ""}
        </span>
        {linkTo && <Link to={linkTo}>{linkLabel || "Open"} <ArrowRight size={11} /></Link>}
      </div>
    </div>
  );
}

function Indicators({ ind, siteId }) {
  const t = ind.technical, v = ind.ai_visibility, tr = ind.traffic, g = ind.ga4, c = ind.conversions, a = ind.action_center;
  const sig = t.signals;
  return (
    <div className="sp-bi-inds" aria-label="Website status by source">
      <IndicatorCard title="Technical audit" icon={ShieldCheck} data={t}
        value={t.score != null ? `${t.score}/100` : null}
        detail={sig ? `${sig.fail} failing · ${sig.warn} warning · ${sig.pass} passing signals` : null}
        linkTo={t.scan_id ? `/app/scans/${t.scan_id}` : null} linkLabel="Audit" />
      <IndicatorCard title="AI answer visibility" icon={MessageSquareText} data={v}
        value={v.mention_rate_pct != null ? `${v.mention_rate_pct}%` : null}
        detail={v.samples_analyzed != null
          ? `of ${v.samples_analyzed} answers to ${v.query_count} tracked queries mention you (${providers(v.providers)})`
          : null}
        linkTo={v.link || `/app/answer-tracking/${siteId}`} linkLabel="Answers" />
      <IndicatorCard title="Website traffic" icon={Activity} data={tr}
        value={tr.current ? fmtNum(tr.current.total_sessions) : null}
        detail={tr.current ? `sessions · ${fmtNum(tr.current.ai_sessions)} from AI assistants` : null} />
      <IndicatorCard title="Google Analytics" icon={BarChart3} data={g}
        value={g.sessions != null ? fmtNum(g.sessions) : null}
        detail={g.sessions != null ? `GA4 sessions · ${fmtNum(g.ai_assistant_sessions)} from AI assistants` : null}
        linkTo={g.link} linkLabel="GA4" />
      <IndicatorCard title="Conversion measurement" icon={Target}
        data={{ ...c, source: c.configured ? "first_party" : c.ga4_key_events_configured ? "ga4" : null,
                reason: "No confirmed conversions or GA4 key events are configured, so business outcomes can't be measured yet." }}
        value={c.status === "available" ? "Configured" : null}
        detail={[c.configured && "AEOMirror confirmed conversions", c.ga4_key_events_configured && "GA4 key events"].filter(Boolean).join(" · ")} />
      <IndicatorCard title="Site crawl findings" icon={Search} data={a}
        value={a.open_total != null ? fmtNum(a.open_total) : null}
        detail={a.open_by_priority ? `open · ${a.open_by_priority.critical || 0} critical, ${a.open_by_priority.high || 0} high` : null}
        linkTo={a.link} linkLabel="Findings" />
    </div>
  );
}

/* ---------------------------------------------------------------- findings */

function Findings({ findings, range }) {
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState(null);
  const visible = showAll ? findings : findings.slice(0, 3);
  return (
    <Cell className="sp-bi-card">
      <h2><Target size={16} /> Priorities</h2>
      {!findings.length ? (
        <p className="sp-bi-empty-inline">
          No evidence-backed findings for this website yet. Findings appear once an audit,
          an Answer Tracking run, or enough tracked traffic exists — nothing is guessed.
        </p>
      ) : (
        <>
          <ol className="sp-bi-findings">
            {visible.map((f) => {
              const isOpen = open === f.id;
              return (
                <li key={f.id} className={`sp-bi-finding pri-${f.priority}`}>
                  <button type="button" className="sp-bi-finding-h" aria-expanded={isOpen}
                          onClick={() => setOpen(isOpen ? null : f.id)}>
                    <span className={`sp-bi-pri pri-${f.priority}`}>{f.priority}</span>
                    <span className="sp-bi-finding-t">{f.title}</span>
                    <span className="sp-bi-finding-cat">{CATEGORY_LABEL[f.category] || f.category}</span>
                    {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  {isOpen && (
                    <div className="sp-bi-finding-b">
                      <ul className="sp-bi-statements">
                        {f.statements.map((s, i) => (
                          <li key={i}><span className={`sp-bi-stype t-${s.type}`}>{STATEMENT_LABEL[s.type]}</span> {s.text}</li>
                        ))}
                      </ul>
                      <dl className="sp-bi-evidence">
                        {f.evidence.map((e, i) => (
                          <React.Fragment key={i}><dt>{e.label}</dt><dd>{e.value} <small>{SOURCE_LABEL[e.source] || e.source}</small></dd></React.Fragment>
                        ))}
                      </dl>
                      {f.calculation && (
                        <p className="sp-bi-finding-meta">
                          Formula: {f.calculation.expression}
                          {` (${Object.entries(f.calculation.inputs).map(([k, v]) => `${k} = ${v}`).join(", ")} → ${f.calculation.result})`}
                        </p>
                      )}
                      <p className="sp-bi-finding-meta">
                        Priority: {f.priority_rationale}
                        {f.sample ? ` · Sample: ${f.sample.size} ${f.sample.unit}` : ""}
                        {` · Confidence: ${f.confidence}`}
                        {f.date_range?.start ? ` · ${f.date_range.start} – ${f.date_range.end}` : ""}
                      </p>
                      {f.link && <Link className="sp-bi-page-link" to={f.link}>Open the evidence <ArrowRight size={11} /></Link>}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
          {findings.length > 3 && (
            <button type="button" className="sp-bi-more" onClick={() => setShowAll(!showAll)}>
              {showAll ? "Show top 3" : `Show all ${findings.length} findings`}
            </button>
          )}
        </>
      )}
    </Cell>
  );
}

/* ---------------------------------------------------------------- setup */

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

/* ---------------------------------------------------------------- performance */

function Delta({ pct }) {
  if (pct == null) return null;
  const dir = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  return <span className={`sp-bi-delta ${dir}`}>{pct > 0 ? "+" : ""}{pct}% vs previous</span>;
}

function SummaryCards({ summary, traffic, onUpgrade }) {
  const ch = traffic?.change_pct || {};
  const conv = summary.conversion_tracking_configured;
  const notMeasured = "Not measured yet";
  const cards = [
    { key: "total_sessions", label: "Website sessions", value: summary.total_sessions?.toLocaleString(), delta: ch.total_sessions, tip: "Every visit to a page with the tracking snippet installed, in this date range." },
    { key: "unique_visitors", label: "Unique visitors", value: summary.unique_visitors?.toLocaleString(), tip: "Distinct first-party visitor IDs in this date range — counted once over the whole range (never summed by day)." },
    { key: "total_page_views", label: "Page views", value: summary.total_page_views?.toLocaleString(), tip: "Every tracked page load, in this date range." },
    { key: "ai_sessions", label: "Sessions from AI", value: summary.ai_sessions?.toLocaleString(), delta: ch.ai_sessions, tip: "Sessions whose first touch was a known AI assistant referral (ChatGPT, Perplexity, Gemini, Copilot, Claude, or another identifiable AI source)." },
    { key: "ai_unique_visitors", label: "Visitors from AI", value: summary.ai_unique_visitors?.toLocaleString(), tip: "Distinct visitors whose first touch was a known AI assistant referral." },
    { key: "ai_share_pct", label: "AI traffic share", value: summary.ai_share_pct != null ? `${summary.ai_share_pct}%` : "—", tip: "AI-referred sessions ÷ all sessions × 100. Shown as — when there were no sessions." },
    { key: "ai_conversions", label: "Confirmed conversions from AI", value: conv ? summary.ai_conversions : notMeasured, locked: !summary.is_pro, tip: "Server-confirmed conversions of any type (purchase, lead, booking, signup, contact, custom) whose session arrived from an AI assistant." },
    // `locked` is keyed on the plan, never on the value being null (a null ratio is a
    // real "no AI sessions" case, not a paywall).
    { key: "ai_conversion_rate_pct", label: "AI conversion rate",
      value: !conv ? notMeasured : summary.ai_conversion_rate_pct != null ? `${summary.ai_conversion_rate_pct}%` : "Not enough data yet",
      locked: !summary.is_pro,
      tip: "Confirmed AI-attributed conversions ÷ AI-referred sessions × 100. Not shown when there were no AI-referred sessions or conversions aren't being confirmed." },
    { key: "ai_revenue_cents", label: "Revenue attributed to AI", value: conv ? money(summary.ai_revenue_cents) : notMeasured, locked: !summary.is_pro, tip: "Confirmed purchase revenue from AI-referred sessions only — never an estimate." },
    { key: "confirmed_bookings", label: "Confirmed bookings (all traffic)", value: conv ? summary.confirmed_bookings : notMeasured, locked: !summary.is_pro, tip: "Bookings confirmed via the server-to-server tracking API, from every channel — not only AI.",
      sub: summary.unverified_bookings > 0 ? `+${summary.unverified_bookings} unverified (visitor-reported, not yet confirmed)` : null },
    { key: "leads", label: "Leads (all traffic)", value: conv ? summary.leads : notMeasured, locked: !summary.is_pro, tip: "Server-confirmed lead submissions from every channel — not merely visits to a contact page.",
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
              <div className={`sp-bi-stat-v${c.value === notMeasured ? " muted" : ""}`}>{c.value ?? "—"}</div>
              {c.delta !== undefined && <Delta pct={c.delta} />}
              {c.sub && <div className="sp-bi-stat-sub">{c.sub}</div>}
            </>
          )}
        </Cell>
      ))}
    </div>
  );
}

function TrafficChart({ series }) {
  // Every day of the range comes from the API (zero-filled server-side). Days before
  // tracking began have tracked=false and total=null: plotted as a gap, never a 0.
  const tracked = series.filter((d) => d.tracked);
  const total = tracked.reduce((n, d) => n + (d.total || 0), 0);
  const data = series.map((d) => ({ date: d.date, ...(d.tracked ? d.by_channel : {}) }));
  const channelsPresent = Array.from(new Set(tracked.flatMap((d) => Object.keys(d.by_channel))));
  // Fill tracked days with explicit zeros per channel so the stack draws real zeros.
  for (const row of data) {
    const src = series.find((d) => d.date === row.date);
    if (src?.tracked) for (const ch of channelsPresent) if (row[ch] == null) row[ch] = 0;
  }
  const untrackedDays = series.length - tracked.length;
  return (
    <Cell className="sp-bi-card">
      <h2><TrendingUp size={16} /> Traffic over time
        <span className="sp-bi-tip" title="Sessions per day (UTC), by first-touch source.">
          <Info size={12} /></span></h2>
      <p className="sp-bi-sub-sm">
        {fmtNum(total)} sessions over {tracked.length} tracked day{tracked.length === 1 ? "" : "s"}
        {untrackedDays > 0 ? ` · ${untrackedDays} earlier day${untrackedDays === 1 ? "" : "s"} before tracking started are left blank` : ""}.
      </p>
      <div className="sp-bi-chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid stroke="var(--sp-line)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={(d) => fmtDate(d)} tick={{ fill: "var(--sp-muted)", fontSize: 10 }}
                   axisLine={false} tickLine={false} minTickGap={24} />
            <YAxis allowDecimals={false} domain={[0, (max) => Math.max(1, max)]} tick={{ fill: "var(--sp-muted)", fontSize: 10 }}
                   axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12 }}
                    labelFormatter={(d) => fmtDate(d)} />
            {channelsPresent.map((ch) => (
              <Area key={ch} type="linear" dataKey={ch} stackId="1" name={CHANNEL_LABELS[ch] || ch} connectNulls={false}
                    stroke={CHANNEL_COLORS[ch] || "var(--sp-muted)"} fill={CHANNEL_COLORS[ch] || "var(--sp-muted)"} fillOpacity={0.45} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
        {total === 0 && <p className="sp-bi-chart-caption">No traffic recorded for this period yet.</p>}
      </div>
      {channelsPresent.length > 0 && (
        <div className="sp-bi-legend">
          {channelsPresent.map((ch) => (
            <span key={ch}><i style={{ background: CHANNEL_COLORS[ch] || "var(--sp-muted)" }} />{CHANNEL_LABELS[ch] || ch}</span>
          ))}
        </div>
      )}
    </Cell>
  );
}

/* ---------------------------------------------------------------- AI search */

function AiSearchIntelligence({ ind, channels, isPro }) {
  const tr = ind?.traffic, v = ind?.ai_visibility, g = ind?.ga4;
  const aiRows = (channels || []).filter((c) => AI_CHANNELS.includes(c.channel) && c.sessions > 0);
  return (
    <Cell className="sp-bi-card">
      <h2><MessageSquareText size={16} /> AI search intelligence</h2>
      <p className="sp-bi-sub-sm">Two different measurements, shown side by side and never combined:
        visits that arrived from an AI assistant, and whether this brand appears in tracked AI answers.</p>
      <div className="sp-bi-ai-cols">
        <div className="sp-bi-ai-col">
          <h3>AI referral traffic <span className="sp-bi-source">AEOMirror tracking</span></h3>
          {tr?.current ? (
            <>
              <p className="sp-bi-ai-big">{fmtNum(tr.current.ai_sessions)} <small>sessions from AI assistants</small></p>
              {isPro && aiRows.length > 0 && (
                <ul className="sp-bi-ai-list">
                  {aiRows.map((c) => <li key={c.channel}><span>{CHANNEL_LABELS[c.channel]}</span><strong>{fmtNum(c.sessions)}</strong></li>)}
                </ul>
              )}
              {g?.sessions != null && (
                <p className="sp-bi-hint">GA4 (linked property): {fmtNum(g.ai_assistant_sessions)} sessions from AI assistants.</p>
              )}
              <p className="sp-bi-hint">Visits with no recognized AI referrer — including all Google organic search — are not counted here.</p>
            </>
          ) : <p className="sp-bi-empty-inline">{tr?.reason || "No tracked traffic yet."}</p>}
        </div>
        <div className="sp-bi-ai-col">
          <h3>AI answer visibility <span className="sp-bi-source">Answer Tracking</span></h3>
          {v && v.mention_rate_pct != null ? (
            <>
              <p className="sp-bi-ai-big">{v.mention_rate_pct}% <small>of analyzed answers mention you</small></p>
              <p className="sp-bi-hint">{v.mentions} mentions in {v.samples_analyzed} answers to {v.query_count} tracked
                queries on {providers(v.providers)}, run {fmtDay(v.as_of)}. A sample of tracked queries, not all AI answers.</p>
              {v.link && <Link className="sp-bi-page-link" to={v.link}>See which queries <ArrowRight size={11} /></Link>}
            </>
          ) : <p className="sp-bi-empty-inline">{v?.reason || "No Answer Tracking results yet."}</p>}
        </div>
      </div>
    </Cell>
  );
}

function ChannelBreakdown({ channels, conversionMeasured }) {
  const data = channels.map((c) => ({ ...c, label: CHANNEL_LABELS[c.channel] || c.channel }));
  return (
    <Cell className="sp-bi-card">
      <h2><Users size={16} /> Traffic by source
        <span className="sp-bi-tip" title="Sessions grouped by first-touch channel — direct, organic search, social, a named AI assistant, or another identifiable referral.">
          <Info size={12} /></span></h2>
      {!data.length ? (
        <p className="sp-bi-empty-inline">No traffic-source data recorded for this period yet.</p>
      ) : (
        <>
          <div className="sp-bi-chart sp-bi-chart-sm">
            <ResponsiveContainer width="100%" height="100%">
              <RBarChart data={data.filter((c) => c.sessions > 0)} margin={{ top: 6, right: 8, bottom: 0, left: -20 }}>
                <CartesianGrid stroke="var(--sp-line)" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: "var(--sp-muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: "var(--sp-muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "var(--sp-solid)", border: "1px solid var(--sp-line)", borderRadius: 12, fontSize: 12 }} />
                <Bar dataKey="sessions" fill="var(--sp-primary)" radius={[4, 4, 0, 0]} />
              </RBarChart>
            </ResponsiveContainer>
          </div>
          <div className="sp-bi-table-wrap">
            <table className="sp-bi-table">
              <thead><tr><th>Source</th><th>Sessions</th><th>Conversions</th><th>Conv. rate</th><th>Revenue</th></tr></thead>
              <tbody>
                {data.map((c) => (
                  <tr key={c.channel} className={AI_CHANNELS.includes(c.channel) ? "ai" : ""}>
                    <td>{c.label}</td><td>{c.sessions.toLocaleString()}</td>
                    <td>{conversionMeasured ? c.conversions : "—"}</td>
                    <td>{conversionMeasured && c.conversion_rate_pct != null ? `${c.conversion_rate_pct}%` : "—"}</td>
                    <td>{conversionMeasured ? money(c.revenue_cents) || "—" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!conversionMeasured && <p className="sp-bi-hint">Conversion columns stay blank until conversions are confirmed from your server.</p>}
        </>
      )}
    </Cell>
  );
}

const PRIORITY_LABEL = { high: "High priority", medium: "Medium priority", low: "Low priority", unknown: "Insufficient data" };

function LandingPageTable({ pages }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("priority");
  const order = { high: 0, medium: 1, low: 2, unknown: 3 };
  const rows = pages
    .filter((p) => !q || (p.path || p.url || "").toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (sort === "sessions" ? b.sessions - a.sessions
      : sort === "ai" ? b.ai_sessions - a.ai_sessions
        : (order[a.priority] ?? 3) - (order[b.priority] ?? 3) || b.sessions - a.sessions));
  return (
    <Cell className="sp-bi-card">
      <h2>Landing pages</h2>
      <p className="sp-bi-sub-sm">Ranked by measured traffic and, where confirmed, conversions. A page's audit
        result is shown alongside as separate evidence — it never changes the traffic-based ranking.
        Never a guaranteed outcome, and never an estimated revenue gain.</p>
      <div className="sp-bi-pages-toolbar">
        <input type="search" aria-label="Search pages" placeholder="Search a page path…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Sort pages" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="priority">Sort: priority</option>
          <option value="sessions">Sort: sessions</option>
          <option value="ai">Sort: AI sessions</option>
        </select>
      </div>
      {!rows.length ? (
        <p className="sp-bi-empty-inline">{pages.length ? "No page matches that search." : "Not enough landing-page data recorded for this period yet."}</p>
      ) : (
      <div className="sp-bi-pages">
        {rows.map((p) => (
          <div key={p.page_key || p.url} className={`sp-bi-page pri-${p.priority}`}>
            <div className="sp-bi-page-top">
              <a href={p.url} target="_blank" rel="noreferrer">{p.path || p.url}</a>
              <span className={`sp-bi-pri pri-${p.priority}`}>{PRIORITY_LABEL[p.priority]}</span>
            </div>
            <div className="sp-bi-page-stats">
              <span>{p.sessions} sessions</span>
              <span>{p.ai_sessions} from AI</span>
              {p.conversion_measured
                ? <span>{p.conversions} conversions ({p.conversion_rate_pct ?? "—"}%)</span>
                : <span>conversions not measured</span>}
              {p.revenue_cents > 0 && <span>{money(p.revenue_cents)} revenue</span>}
              {p.variant_count > 1 && <span title="Visits to the same page with different query strings, trailing slashes or www are combined.">{p.variant_count} URL variants combined</span>}
            </div>
            {p.recommendation && <p className="sp-bi-page-rec">{p.recommendation}</p>}
            {p.reason && <p className="sp-bi-page-reason">Why: {p.reason}</p>}
            {p.aeo ? (
              <p className="sp-bi-page-audit">
                Audit {p.aeo.scanned_at ? `(${fmtDate(p.aeo.scanned_at)})` : ""}: {p.aeo.score}/100 · {p.aeo.issue_count} issue{p.aeo.issue_count === 1 ? "" : "s"}
                {p.aeo.failing_signals?.length ? ` · failing: ${p.aeo.failing_signals.join(", ")}` : ""}
                {" "}<Link className="sp-bi-page-link" to={`/app/scans/${p.aeo.scan_id}`}>View audit →</Link>
              </p>
            ) : <p className="sp-bi-page-audit muted">No audit of this exact page yet.</p>}
          </div>
        ))}
      </div>
      )}
    </Cell>
  );
}
