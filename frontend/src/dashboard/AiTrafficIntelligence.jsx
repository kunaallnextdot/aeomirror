/* AI Traffic & Conversion Intelligence sections for the AI Traffic dashboard. Every
   number comes from GET /integrations/google/intelligence (synced GA4 data — no live
   Google call) and /events; nothing is estimated here. Conversions that aren't
   measured read "Not measured", never 0; revenue appears only when GA4 reported it
   and the property's currency is known. Attribution is session-level: GA4's data
   API is aggregated, so there is no per-user journey to show. */
import React, { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, Info, Settings2 } from "lucide-react";
import { saveGa4EventMappings, ScanError } from "../api.js";
import { fmtDate } from "./ui.jsx";
import { Cell, Button } from "../shared/spectrum.jsx";

const nf = (n) => (n == null ? "—" : Number(n).toLocaleString());
const pct = (n) => (n == null ? "—" : `${n}%`);
export function money(v, currency) {
  if (v == null || !currency) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(v);
  } catch {
    return `${Math.round(v).toLocaleString()} ${currency}`;
  }
}
const TYPE_LABEL = { purchase: "Purchases", lead: "Leads", booking: "Bookings", signup: "Sign-ups", custom: "Custom" };
const QUALITY_NOTES = {
  thresholded: "GA4 withheld some rows to protect user privacy (thresholding), so small numbers may be missing.",
  sampled: "GA4 sampled this data, so the figures are estimates from a subset of sessions.",
  other_row: "GA4 merged its long tail into an \"(other)\" row, so some low-volume sources or pages are grouped together.",
  truncated: "The report exceeded the row limit synced per day; the least-visited rows were not stored.",
};
const REPORT_LABEL = { traffic: "traffic", landing_pages: "landing-page", events: "events" };

/* ------------------------------------------------------------ freshness */

export function FreshnessNote({ intel }) {
  const f = intel?.freshness;
  if (!f) return null;
  const flags = [];
  for (const [report, q] of Object.entries(f.data_quality || {})) {
    for (const [k, on] of Object.entries(q || {})) if (on && QUALITY_NOTES[k]) flags.push(`${QUALITY_NOTES[k]} (${REPORT_LABEL[report] || report} report)`);
  }
  return (
    <div className="sp-ga4-freshness" role="status">
      <span>Last successful sync {f.last_synced_at ? fmtDate(f.last_synced_at) : "—"}
        {f.events_synced_at ? ` · events ${fmtDate(f.events_synced_at)}` : " · events not synced yet"}</span>
      {f.stale && <span className="warn"><AlertTriangle size={12} /> Data is more than 2 days old.</span>}
      {f.partial_range && <span className="warn"><Info size={12} /> Synced history starts {f.covered_from}; earlier days in this range aren't included.</span>}
      {f.processing_lag_from && <span><Info size={12} /> GA4 may still be processing data from {f.processing_lag_from} onward.</span>}
      {flags.map((t) => <span key={t} className="warn"><AlertTriangle size={12} /> {t}</span>)}
    </div>
  );
}

/* ------------------------------------------------------------ 1. overview strip */

export function ClassStrip({ intel }) {
  const o = intel?.overview;
  if (!o) return null;
  const items = [
    ["Sessions", nf(o.total_sessions), "All sessions in this period."],
    ["AI assistant sessions", nf(o.ai_sessions), "Sessions whose referrer was a recognised AI assistant."],
    ["AI share", pct(o.ai_share_pct), "AI assistant sessions ÷ all sessions × 100."],
    ["Organic search sessions", nf(o.organic_search_sessions), "Includes any Google AI Overviews clicks — GA4 can't separate them."],
    ["Conversions", o.total_conversions == null ? "Not measured" : nf(o.total_conversions), "Event count of the events chosen as conversions."],
  ];
  return (
    <div className="sp-ga4-kpis sp-ga4-classstrip">
      {items.map(([label, value, tip]) => (
        <div key={label} className="sp-ga4-kpi" title={tip}>
          <div className="sp-ga4-kpi-label">{label}</div>
          <div className={`sp-ga4-kpi-value${value === "Not measured" ? " sp-ga4-kpi-muted" : ""}`}>{value}</div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ 2. conversions */

export function ConversionPerformance({ intel, events, canManage, onSaved }) {
  const [open, setOpen] = useState(false);
  const c = intel?.conversions;
  if (!c) return null;
  const cur = intel.currency;
  const def = c.definition;
  const g = c.groups;
  return (
    <Cell className="sp-ga4-card">
      <h2>Conversion performance</h2>
      {c.status === "not_synced" && (
        <p className="sp-ga4-empty-inline">Event data hasn't synced yet. It arrives with the next sync.</p>
      )}
      {c.status === "not_configured" && (
        <p className="sp-ga4-empty-inline">
          Conversions aren't measured yet: no events are mapped as conversions here, and this GA4
          property has no key events. Choose which of your events count as conversions below.
        </p>
      )}
      {c.status === "available" && (
        <>
          <p className="sp-ga4-helper">
            {def.mode === "mapped"
              ? <>Counting <strong>{def.events.join(", ")}</strong> (chosen in AEOMirror).</>
              : <>Counting your GA4 key events: <strong>{def.events.join(", ")}</strong>. Map events below to choose different ones.</>}
          </p>
          <div className="sp-ga4-conv-compare">
            {["ai", "organic_search"].map((k) => (
              <div key={k} className="sp-ga4-conv-col">
                <h3>{g[k].label}</h3>
                <dl>
                  <dt>Sessions</dt><dd>{nf(g[k].sessions)}</dd>
                  <dt>Conversions</dt><dd>{nf(g[k].conversions)}</dd>
                  <dt title={intel.definitions.conversions_per_100_sessions}>Per 100 sessions</dt>
                  <dd>{g[k].conversions_per_100_sessions ?? "—"}</dd>
                  {c.revenue_status === "available" && <><dt>Revenue</dt><dd>{money(g[k].revenue, cur)}</dd></>}
                </dl>
              </div>
            ))}
            <div className="sp-ga4-conv-col">
              <h3>By type (all sources)</h3>
              <dl>
                {Object.entries(c.by_type).map(([t, n]) => <React.Fragment key={t}><dt>{TYPE_LABEL[t] || t}</dt><dd>{nf(n)}</dd></React.Fragment>)}
                <dt><strong>Total</strong></dt><dd><strong>{nf(c.total)}</strong></dd>
              </dl>
            </div>
          </div>
          {c.revenue_status !== "available" && (
            <p className="sp-ga4-helper">
              {c.revenue_status === "no_currency"
                ? "Revenue isn't shown because the property's currency couldn't be read from GA4."
                : "No revenue is reported by this GA4 property for this period."}
            </p>
          )}
          {c.funnel && (
            <div className="sp-ga4-funnel" aria-label="Ecommerce funnel">
              {c.funnel.map((s) => (
                <div key={s.event_name}><span>{s.event_name}</span><strong>{nf(s.event_count)}</strong></div>
              ))}
              <p className="sp-ga4-helper">Event counts, not unique users — one visitor can add to cart several times.</p>
            </div>
          )}
        </>
      )}
      <p className="sp-ga4-helper sp-ga4-definition">{intel.definitions.attribution}</p>
      <button type="button" className="sp-ga4-disclose" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Settings2 size={13} /> {canManage ? "Configure conversions" : "Event availability"} {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>
      {open && <EventMappingEditor events={events} canManage={canManage} onSaved={onSaved} />}
    </Cell>
  );
}

function initialMap(events) {
  const m = {};
  for (const e of events?.events || []) {
    if (e.mapping) m[e.event_name] = { ...e.mapping };
  }
  return m;
}

export function EventMappingEditor({ events, canManage, onSaved }) {
  const [map, setMap] = useState(() => initialMap(events));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);
  useEffect(() => { setMap(initialMap(events)); }, [events]);
  const rows = useMemo(() => (events?.events || []).filter((e) => !e.automatic || e.mapping), [events]);
  if (!events) return <p className="sp-ga4-empty-inline">Loading your events…</p>;
  if (!events.synced) return <p className="sp-ga4-empty-inline">Event data hasn't synced yet.</p>;

  const setRow = (name, patch) => setMap((m) => {
    const next = { ...m };
    if (patch === null) delete next[name];
    else next[name] = { ...(next[name] || {}), ...patch };
    return next;
  });
  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const mappings = Object.entries(map).map(([event_name, v]) => ({ event_name, ...v }));
      await saveGa4EventMappings(mappings);
      setMsg({ ok: true, text: "Saved. Conversions now use these events." });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ScanError ? e.message : "Could not save the mapping." });
    } finally { setSaving(false); }
  };

  return (
    <div className="sp-ga4-mapping">
      <p className="sp-ga4-helper">{events.note}</p>
      <table className="sp-ga4-table">
        <thead><tr><th>Event</th><th>Seen</th><th>GA4 key event</th><th>Counts as</th></tr></thead>
        <tbody>
          {rows.map((e) => {
            const m = map[e.event_name];
            const value = m ? (m.role === "conversion" ? `conversion:${m.conversion_type}` : "funnel_step") : "";
            return (
              <tr key={e.event_name}>
                <td><code>{e.event_name}</code></td>
                <td>{e.seen_in_range ? nf(e.event_count) : "not in this period"}</td>
                <td>{e.is_key_event == null ? "unknown" : e.is_key_event ? "yes" : "no"}</td>
                <td>
                  {canManage ? (
                    <select aria-label={`How ${e.event_name} counts`} value={value}
                            onChange={(ev) => {
                              const v = ev.target.value;
                              if (!v) return setRow(e.event_name, null);
                              if (v === "funnel_step") return setRow(e.event_name, { role: "funnel_step", conversion_type: null, counts_revenue: false });
                              const t = v.split(":")[1];
                              return setRow(e.event_name, { role: "conversion", conversion_type: t, counts_revenue: t === "purchase" });
                            }}>
                      <option value="">Not counted</option>
                      <option value="conversion:purchase">Conversion · purchase</option>
                      <option value="conversion:lead">Conversion · lead</option>
                      <option value="conversion:booking">Conversion · booking</option>
                      <option value="conversion:signup">Conversion · sign-up</option>
                      <option value="conversion:custom">Conversion · custom</option>
                      <option value="funnel_step">Funnel step</option>
                    </select>
                  ) : (value ? value.replace(":", " · ") : "Not counted")}
                  {!m && e.suggestion && canManage && (
                    <button type="button" className="sp-ga4-suggest"
                            onClick={() => setRow(e.event_name, e.suggestion)}>
                      Suggested: {e.suggestion.role === "funnel_step" ? "funnel step" : e.suggestion.conversion_type}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h3 className="sp-ga4-subhead">Standard events on this property</h3>
      <ul className="sp-ga4-standard">
        {events.standard_events.map((s) => (
          <li key={s.event_name} className={s.status}>
            <code>{s.event_name}</code> {s.status === "collected" ? "collected" : "not seen — needs website-side tracking"}
          </li>
        ))}
      </ul>
      {canManage && (
        <div className="sp-ga4-mapping-actions">
          <Button variant="gradient" onClick={save} loading={saving}>Save conversion mapping</Button>
          {msg && <span className={msg.ok ? "ok" : "err"} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ 4. source comparison */

export function SourceComparison({ intel, children }) {
  const [open, setOpen] = useState(false);
  const rows = intel?.comparison || [];
  const measured = intel?.conversions?.measured;
  const showRevenue = intel?.conversions?.revenue_status === "available";
  return (
    <Cell className="sp-ga4-card">
      <h2>Traffic source comparison</h2>
      {!rows.length ? <p className="sp-ga4-empty-inline">No sessions recorded for this period.</p> : (
        <div className="sp-ga4-table-wrap">
          <table className="sp-ga4-table">
            <thead>
              <tr><th>Source</th><th>Sessions</th><th title={intel.definitions.share_pct}>Share</th><th>Engagement</th>
                <th>Conversions</th><th title={intel.definitions.conversions_per_100_sessions}>Per 100 sessions</th>
                {showRevenue && <th>Revenue</th>}</tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.class} className={["chatgpt", "perplexity", "gemini", "claude", "copilot", "other_ai"].includes(r.class) ? "ai" : ""}>
                  <td>{r.label}</td><td>{nf(r.sessions)}</td><td>{pct(r.share_pct)}</td><td>{pct(r.engagement_rate_pct)}</td>
                  <td>{measured ? nf(r.conversions) : "Not measured"}</td>
                  <td>{measured ? (r.conversions_per_100_sessions ?? "—") : "—"}</td>
                  {showRevenue && <td>{money(r.revenue, intel.currency)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="sp-ga4-helper">{intel?.definitions?.ai_traffic}</p>
      {children && (
        <>
          <button type="button" className="sp-ga4-disclose" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            Source / medium detail {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
          {open && children}
        </>
      )}
    </Cell>
  );
}
