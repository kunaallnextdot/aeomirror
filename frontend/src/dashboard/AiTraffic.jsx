/* AI Traffic (P0→P1) — Google Analytics 4 connection + Website Intelligence
   dashboard. A SEPARATE, inbound-analytics capability from Answer Tracking/Answer
   Simulator (which test how AI assistants answer queries about a site, not real
   visitor traffic). Never conflate the two in this file's copy.

   Every connection-state branch below (not_configured/not_available/not_connected/
   revoked/pending_property/error) is unchanged from the P0 version — only the
   "active" branch grew, from a single "sessions by source" list into a full
   dashboard (Overview, Trends, Channels, Landing Pages, AI Traffic Intelligence,
   Insights). See app/api/routes_integrations.py for the backend: four new/extended
   endpoints (/overview, /trends, /channels, /landing-pages) plus /traffic itself,
   all org-scoped and Pro-gated exactly like before.

   IMPORTANT, non-obvious correctness rule carried over from the backend: GA4's
   "users" metric is NOT additive — summing per-day or per-channel user counts can
   double-count a visitor. /overview is therefore the one endpoint that queries GA4
   live for a correctly-scoped total; /trends, /channels, and /landing-pages read
   stored snapshots and deliberately never show a "Users" total anywhere except the
   Overview cards and the Trends chart's per-day line (each day's own number, never
   summed). Don't "fix" this by summing a per-row Users column — it would be wrong. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, BarChart3, ChevronDown, ChevronUp, Info, Lock,
  RefreshCw, Repeat, Search, Unplug,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer,
  Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";
import {
  connectGoogleAnalytics, disconnectGoogleAnalytics, getGa4Channels, getGa4Events,
  getGa4Intelligence, getGa4LandingPages, getGa4Overview, getGa4Status, getGa4Traffic,
  getGa4Trends, listGa4Properties, selectGa4Property, triggerGa4Sync, ScanError,
} from "../api.js";
import {
  ClassStrip, ConversionPerformance, FreshnessNote, SourceComparison,
} from "./AiTrafficIntelligence.jsx";
import { fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import GoogleConsentModal from "./GoogleConsentModal.jsx";
// GoogleConsentModal.jsx's au-gcm-* rules have moved into AiTraffic.spectrum.css
// (final-integration pass) — the Aurora stylesheet is no longer needed here.
import "./AiTraffic.spectrum.css";

// Mirrors classify_source() in backend/app/services/integrations/classification.py.
const AI_LABELS = {
  chatgpt: "ChatGPT", perplexity: "Perplexity", gemini: "Gemini", copilot: "Copilot",
  claude: "Claude", other_ai: "Other AI assistants", other_identifiable: "Other identified sources",
  unattributable: "Unattributable",
};
const AI_ENGINE_ORDER = ["chatgpt", "perplexity", "gemini", "copilot", "claude", "other_ai"];
const AI_OTHER_ORDER = ["other_identifiable", "unattributable"];

const ERROR_MESSAGES = {
  access_denied: "You cancelled the Google sign-in.",
  invalid_state: "That connection link expired or was already used. Try connecting again.",
  token_exchange_failed: "Google couldn't be reached to finish connecting. Try again.",
  missing_refresh_token: "Google didn't grant the access this needs — approve access when reconnecting.",
  auth_revoked: "Your Google Analytics access was revoked or expired. Reconnect to keep syncing.",
  properties_unavailable: "Could not load your Google Analytics properties right now.",
  property_not_accessible: "That property isn't accessible to the connected Google account.",
  credential_unavailable: "Your Google Analytics connection needs to be reconnected.",
  quota_exceeded: "Google Analytics' request limit was reached. The next sync retries automatically.",
  property_inaccessible: "The selected property is no longer accessible to the connected account.",
  integration_disabled: "Google Analytics isn't configured on this server yet.",
};
const errorMessage = (code) => ERROR_MESSAGES[code] || "Something went wrong with Google Analytics.";

const RANGES = [
  { label: "7 days", days: 7 }, { label: "28 days", days: 28 },
  { label: "90 days", days: 90 }, { label: "Custom", days: "custom" },
];

function isoDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
const todayIso = () => new Date().toISOString().slice(0, 10);

const fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString());
const fmtPct = (n) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`);
const fmtSecs = (n) => {
  if (n == null) return "—";
  const m = Math.floor(n / 60), s = Math.round(n % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
};
// The latest value once it has stopped changing for `ms` (search-as-you-type).
function useDebouncedValue(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const fmtDelta = (curr, prev) => {
  if (curr == null || prev == null) return null;
  if (prev === 0) return curr === 0 ? null : { dir: "up", text: "new" };
  const pct = ((curr - prev) / prev) * 100;
  if (Math.abs(pct) < 0.5) return null;
  return { dir: pct > 0 ? "up" : "down", text: `${pct > 0 ? "+" : ""}${pct.toFixed(0)}%` };
};

export default function AiTraffic() {
  const { hasPermission } = useAuth();
  const { openUpgrade } = useUpgrade();
  const canManage = hasPermission("org:update");

  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(null);
  const [traffic, setTraffic] = useState(null);
  const [properties, setProperties] = useState(null);
  const [showPropertyPicker, setShowPropertyPicker] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null);
  const [showConsent, setShowConsent] = useState(false);
  const [consentError, setConsentError] = useState(null);

  // ---- date range + comparison ----
  const [rangeDays, setRangeDays] = useState(28);
  const [customStart, setCustomStart] = useState(isoDaysAgo(27));
  const [customEnd, setCustomEnd] = useState(todayIso());
  const [compareEnabled, setCompareEnabled] = useState(true);
  const { startDate, endDate } = useMemo(() => {
    if (rangeDays === "custom") return { startDate: customStart, endDate: customEnd };
    // N days INCLUDING today (e.g. 28 days = today and the 27 before it) — the same
    // inclusive window as Business Impact and the backend default.
    return { startDate: isoDaysAgo(rangeDays - 1), endDate: todayIso() };
  }, [rangeDays, customStart, customEnd]);

  // ---- dashboard data ----
  const [overview, setOverview] = useState(null);
  const [trends, setTrends] = useState(null);
  const [channels, setChannels] = useState(null);
  const [landingPages, setLandingPages] = useState(null);
  const [intel, setIntel] = useState(null);      // /intelligence: the four sections' figures
  const [events, setEvents] = useState(null);    // /events: availability + conversion mapping
  const [mappingVersion, setMappingVersion] = useState(0);   // bumped after a mapping save
  const [dashLoading, setDashLoading] = useState(false);
  const [dashError, setDashError] = useState(null);
  const [sectionErrors, setSectionErrors] = useState({});
  const [channelSort, setChannelSort] = useState("-sessions");
  const [channelQuery, setChannelQuery] = useState("");
  const [landingSort, setLandingSort] = useState("-sessions");
  const [landingQuery, setLandingQuery] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const s = await getGa4Status();
      setStatus(s);
      return s;
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load Google Analytics status.");
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const ga4Param = params.get("ga4");
      if (ga4Param === "connected") setBanner({ kind: "ok", text: "Google Analytics connected." });
      else if (ga4Param === "error") setBanner({ kind: "error", text: errorMessage(params.get("reason")) });
      if (ga4Param) {
        params.delete("ga4"); params.delete("reason");
        setParams(params, { replace: true });
      }
      await loadStatus();
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Table search is debounced, and a table's sort/search refetches only that table —
  // not the whole dashboard, whose /overview is a live (quota-counted) GA4 request.
  const channelQ = useDebouncedValue(channelQuery, 300);
  const landingQ = useDebouncedValue(landingQuery, 300);
  const tableParams = useRef(null);
  tableParams.current = { channelSort, channelQ, landingSort, landingQ };
  // Everything the dashboard's data depends on besides table sort/search: a different
  // property, a newer sync, or another date range must never show the previous data.
  const dataKey = status?.status === "active"
    ? `${status.ga4_property_id}|${status.last_synced_at}|${startDate}|${endDate}|${mappingVersion}` : null;
  const loadedKey = useRef(null);   // dataKey once the full load has issued its table requests
  // Per-table request counter: only the newest request's response is ever applied, so
  // a slow older one (previous range, sort or search) can't overwrite newer data.
  const tableSeq = useRef({ channels: 0, landingPages: 0 });

  // Full reload whenever dataKey changes. getGa4Traffic is included here (not fetched
  // once on mount) so the AI-source breakdown respects the selected date range.
  useEffect(() => {
    loadedKey.current = null;
    if (!dataKey) return;
    let cancelled = false;
    (async () => {
      setDashLoading(true); setDashError(null); setSectionErrors({});
      try {
        const rangeParams = { startDate, endDate };
        const tr = await getGa4Traffic(rangeParams);
        if (cancelled) return;
        setTraffic(tr);
        if (!tr?.synced) {
          setOverview(null); setTrends(null); setChannels(null); setLandingPages(null);
          setIntel(null); setEvents(null);
          setDashLoading(false); return;
        }
        const t = tableParams.current;
        const seq = { channels: ++tableSeq.current.channels, landingPages: ++tableSeq.current.landingPages };
        loadedKey.current = dataKey;
        // allSettled, not all: each section degrades on its own. /overview is the one
        // live GA4 call, so it's the likeliest to fail — it must never blank Trends,
        // Channels, or Landing Pages, which read independently synced snapshots.
        const settled = await Promise.allSettled([
          getGa4Overview(rangeParams),
          getGa4Trends(rangeParams),
          getGa4Channels({ ...rangeParams, sort: t.channelSort, q: t.channelQ || undefined }),
          getGa4LandingPages({ ...rangeParams, sort: t.landingSort, q: t.landingQ || undefined }),
          getGa4Intelligence(rangeParams),
          getGa4Events(rangeParams),
        ]);
        if (cancelled) return;
        const [ov, trend, ch, lp, it, ev] = settled.map((r) => (r.status === "fulfilled" ? r.value : null));
        const reason = (r, fallback) => (r.status === "rejected"
          ? (r.reason instanceof ScanError ? r.reason.message : fallback) : null);
        const fresh = (k) => tableSeq.current[k] === seq[k];   // no newer table request since
        setOverview(ov); setTrends(trend); setIntel(it?.synced ? it : null); setEvents(ev);
        if (fresh("channels")) setChannels(ch);
        if (fresh("landingPages")) setLandingPages(lp);
        setSectionErrors((s) => ({
          overview: reason(settled[0], "Could not load the overview."),
          trends: reason(settled[1], "Could not load traffic trends."),
          channels: fresh("channels") ? reason(settled[2], "Could not load acquisition channels.") : s.channels,
          landingPages: fresh("landingPages") ? reason(settled[3], "Could not load landing pages.") : s.landingPages,
          intelligence: reason(settled[4], "Could not load conversion and source comparison data."),
        }));
      } catch (e) {
        if (!cancelled) {
          setDashError(e instanceof ScanError ? e.message : "Could not load your Website Intelligence dashboard.");
        }
      } finally {
        if (!cancelled) setDashLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey]);

  // One table's sort/search → only that table's request, once the full load for the
  // current dataKey has run (before that, the full load fetches the tables itself).
  const refetchTable = (fetcher, params, key, fallback, setter) => {
    if (!dataKey || loadedKey.current !== dataKey) return undefined;
    const seq = ++tableSeq.current[key];
    const current = () => tableSeq.current[key] === seq && loadedKey.current === dataKey;
    fetcher({ startDate, endDate, ...params })
      .then((r) => { if (current()) { setter(r); setSectionErrors((s) => ({ ...s, [key]: null })); } })
      .catch((e) => {
        if (current()) setSectionErrors((s) => ({ ...s, [key]: e instanceof ScanError ? e.message : fallback }));
      });
    return undefined;
  };
  useEffect(() => refetchTable(getGa4Channels, { sort: channelSort, q: channelQ || undefined }, "channels",
                               "Could not load acquisition channels.", setChannels),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [channelSort, channelQ]);
  useEffect(() => refetchTable(getGa4LandingPages, { sort: landingSort, q: landingQ || undefined }, "landingPages",
                               "Could not load landing pages.", setLandingPages),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [landingSort, landingQ]);

  const beginOAuth = async () => {
    setBusy(true); setError(null); setConsentError(null);
    try {
      const { authorize_url } = await connectGoogleAnalytics();
      if (!authorize_url) throw new ScanError("Could not start the connection.", 500);
      window.location.assign(authorize_url);   // real browser navigation to Google
      // No setBusy(false) on success — the browser is navigating away from this page.
    } catch (e) {
      const msg = e instanceof ScanError ? e.message : "Could not start the connection.";
      if (showConsent) setConsentError(msg); else setError(msg);
      setBusy(false);
    }
  };

  const openConsent = () => { setConsentError(null); setShowConsent(true); };
  const closeConsent = () => { if (!busy) setShowConsent(false); };

  const loadProperties = async () => {
    setBusy(true); setError(null);
    try {
      const { properties: list } = await listGa4Properties();
      setProperties(list);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load properties.");
    } finally { setBusy(false); }
  };

  const pickProperty = async (propertyId) => {
    setBusy(true); setError(null);
    try {
      await selectGa4Property(propertyId);
      await loadStatus();
      setProperties(null);
      setShowPropertyPicker(false);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not select that property.");
    } finally { setBusy(false); }
  };

  const syncNow = async () => {
    setBusy(true); setError(null);
    try {
      await triggerGa4Sync();
      setBanner({ kind: "ok", text: "Sync started — this can take a moment." });
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not start a sync.");
    } finally { setBusy(false); }
  };

  const disconnect = async () => {
    if (!window.confirm("Disconnect Google Analytics? Stored traffic history stays, but syncing stops.")) return;
    setBusy(true); setError(null);
    try {
      await disconnectGoogleAnalytics();
      setStatus({ status: "not_connected" });
      setTraffic(null); setOverview(null); setTrends(null); setChannels(null); setLandingPages(null);
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not disconnect.");
    } finally { setBusy(false); }
  };

  if (loading) {
    return (
      <Shell className="sp-ga4">
        <Skeleton h={160} />
      </Shell>
    );
  }

  return (
    <>
    <Shell className="sp-ga4">
      <header className="sp-ga4-head">
        <h1><BarChart3 size={20} /> AI Traffic</h1>
        <p className="sp-ga4-sub">
          A Website Intelligence dashboard built on your connected Google Analytics
          property: how your site is performing, where visitors come from, and how
          much of that traffic can be attributed to AI assistants — separate from
          Answer Tracking, which tests how AI assistants answer queries about your
          site, not real visitor traffic.
        </p>
      </header>

      {banner && (
        <div className={`sp-ga4-banner sp-ga4-banner-${banner.kind}`}>{banner.text}</div>
      )}
      {error && <div className="sp-ga4-banner sp-ga4-banner-error">{error}</div>}

      {status?.status === "not_configured" && (
        <Cell className="sp-ga4-card">
          <Info size={18} />
          <p>
            Google Analytics connection is temporarily unavailable because AEOMirror
            hasn't completed its integration setup. This isn't something you can fix
            from here — check back later, or reach out if it persists.
          </p>
        </Cell>
      )}

      {status?.status === "not_available" && (
        <Cell className="sp-ga4-card">
          <Lock size={18} />
          <p>AI Traffic is a Pro feature.</p>
          <Button variant="gradient" onClick={() => openUpgrade("ai_traffic")}>Upgrade</Button>
        </Cell>
      )}

      {status?.status === "not_connected" && canManage && (
        <Cell className="sp-ga4-card">
          <p>Connect your Google Analytics account to see which AI assistants are sending you traffic.</p>
          <Button variant="gradient" onClick={openConsent} loading={busy}>Connect Google Analytics</Button>
        </Cell>
      )}

      {status?.status === "revoked" && (
        <Cell className="sp-ga4-card">
          <Unplug size={18} />
          <p>Google Analytics was disconnected and is no longer syncing.</p>
          {canManage ? (
            <Button variant="gradient" onClick={openConsent} loading={busy}>Connect Google Analytics</Button>
          ) : (
            <p>Ask an organization owner or admin to reconnect it.</p>
          )}
        </Cell>
      )}

      {status?.status === "pending_property" && canManage && (
        <Cell className="sp-ga4-card">
          <p>Connected. Choose the GA4 property to track.</p>
          {!properties ? (
            <Button variant="gradient" onClick={loadProperties} loading={busy}>Load properties</Button>
          ) : properties.length === 0 ? (
            <>
              <p>
                No GA4 properties are accessible to that Google account. This usually
                means either the Google account you signed in with doesn't have access
                to a GA4 property, or you meant to sign in with a different Google
                account. Check Admin → Property Access Management in Google Analytics
                for the account you expect to use, or try connecting again and pick a
                different Google account.
              </p>
              <Button variant="gradient" onClick={beginOAuth} loading={busy}>Try a different Google account</Button>
            </>
          ) : (
            <ul className="sp-ga4-proplist">
              {properties.map((p) => (
                <li key={p.property_id}>
                  <button type="button" disabled={busy} onClick={() => pickProperty(p.property_id)}>
                    {p.property_name} <span className="sp-ga4-prop-account">{p.account_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Cell>
      )}

      {status?.status === "error" && (
        <Cell className="sp-ga4-card">
          <p>{errorMessage(status.last_sync_error_code)}</p>
          {canManage && <Button variant="gradient" onClick={beginOAuth} loading={busy}>Reconnect</Button>}
        </Cell>
      )}

      {status?.status === "active" && (
        <>
          <Cell className="sp-ga4-card sp-ga4-toolbar">
            <div>
              <strong>{status.ga4_property_name}</strong>
              <span className="sp-ga4-propid"> · {status.ga4_property_id}</span>
              <div className="sp-ga4-synced">
                {status.last_synced_at
                  ? `Last synced ${fmtDate(status.last_synced_at)}`
                  : "Not synced yet"}
              </div>
            </div>
            <div className="sp-ga4-ranges">
              {RANGES.map((r) => (
                <button key={r.label} className={rangeDays === r.days ? "on" : ""}
                        onClick={() => setRangeDays(r.days)}>{r.label}</button>
              ))}
            </div>
            {canManage && (
              <div className="sp-ga4-actions">
                <Button variant="ghost" onClick={() => { setShowPropertyPicker((v) => !v); if (!properties) loadProperties(); }}>
                  <Repeat size={14} /> Change property
                </Button>
                <Button variant="ghost" onClick={syncNow} loading={busy}>
                  <RefreshCw size={14} /> Sync now
                </Button>
                <Button variant="ghost" onClick={disconnect} loading={busy}>
                  <Unplug size={14} /> Disconnect
                </Button>
              </div>
            )}
          </Cell>

          {rangeDays === "custom" && (
            <Cell className="sp-ga4-card sp-ga4-customrange">
              <label>From <input type="date" value={customStart} max={customEnd}
                                  onChange={(e) => setCustomStart(e.target.value)} /></label>
              <label>To <input type="date" value={customEnd} min={customStart} max={todayIso()}
                                onChange={(e) => setCustomEnd(e.target.value)} /></label>
              <label className="sp-ga4-compare-toggle">
                <input type="checkbox" checked={compareEnabled}
                       onChange={(e) => setCompareEnabled(e.target.checked)} />
                Compare to previous period
              </label>
            </Cell>
          )}
          {rangeDays !== "custom" && (
            <div className="sp-ga4-compare-row">
              <label className="sp-ga4-compare-toggle">
                <input type="checkbox" checked={compareEnabled}
                       onChange={(e) => setCompareEnabled(e.target.checked)} />
                Compare to previous period
              </label>
            </div>
          )}

          {showPropertyPicker && canManage && (
            <Cell className="sp-ga4-card">
              <h2>Switch property</h2>
              {!properties ? (
                <Skeleton h={60} />
              ) : properties.length === 0 ? (
                <p>No other GA4 properties are accessible to the connected account.</p>
              ) : (
                <ul className="sp-ga4-proplist">
                  {properties.map((p) => (
                    <li key={p.property_id}>
                      <button type="button" disabled={busy} onClick={() => pickProperty(p.property_id)}>
                        {p.property_name} <span className="sp-ga4-prop-account">{p.account_name}</span>
                        {p.property_id === status.ga4_property_id && <span className="sp-ga4-current"> (current)</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Cell>
          )}

          {!traffic?.synced ? (
            <Cell className="sp-ga4-card">
              <p>Sync pending — the first sync can take a few minutes after connecting.</p>
            </Cell>
          ) : dashError ? (
            <Cell className="sp-ga4-card sp-ga4-banner-error">
              <AlertTriangle size={16} /> <p>{dashError}</p>
            </Cell>
          ) : dashLoading && !overview ? (
            <Skeleton h={400} />
          ) : (
            <>
              <FreshnessNote intel={intel} />

              {/* 1. Traffic overview */}
              <section aria-labelledby="ga4-s1" className="sp-ga4-section">
                <h2 id="ga4-s1" className="sp-ga4-section-title">Traffic overview</h2>
                {sectionErrors.intelligence ? null : <ClassStrip intel={intel} />}
                {sectionErrors.overview
                  ? <SectionError title="Executive overview" message={sectionErrors.overview} />
                  : <OverviewCards overview={overview} compareEnabled={compareEnabled} loading={dashLoading} />}
                {sectionErrors.trends
                  ? <SectionError title="Traffic trends" message={sectionErrors.trends} />
                  : <TrendsSection trends={trends} />}
              </section>

              {/* 2. Conversion performance */}
              <section aria-label="Conversion performance" className="sp-ga4-section">
                {sectionErrors.intelligence
                  ? <SectionError title="Conversion performance" message={sectionErrors.intelligence} />
                  : <ConversionPerformance intel={intel} events={events} canManage={canManage}
                                           onSaved={() => setMappingVersion((v) => v + 1)} />}
              </section>

              {/* 3. Top landing pages */}
              <section aria-label="Top landing pages" className="sp-ga4-section">
                {sectionErrors.landingPages
                  ? <SectionError title="Top landing pages" message={sectionErrors.landingPages} />
                  : <LandingPagesTable landingPages={landingPages} sort={landingSort} setSort={setLandingSort}
                                        query={landingQuery} setQuery={setLandingQuery} />}
              </section>

              {/* 4. Traffic source comparison (source/medium detail disclosed on demand) */}
              <section aria-label="Traffic source comparison" className="sp-ga4-section">
                {sectionErrors.intelligence
                  ? <SectionError title="Traffic source comparison" message={sectionErrors.intelligence} />
                  : (
                    <SourceComparison intel={intel}>
                      {sectionErrors.channels
                        ? <p className="sp-ga4-empty-inline">{sectionErrors.channels}</p>
                        : <ChannelsTable embedded channels={channels} sort={channelSort} setSort={setChannelSort}
                                         query={channelQuery} setQuery={setChannelQuery} />}
                    </SourceComparison>
                  )}
              </section>
            </>
          )}

          <Cell className="sp-ga4-card sp-ga4-note">
            <Info size={16} />
            <p>
              AI-source counts only cover traffic Google Analytics can identify by
              referrer. Some AI traffic may appear as direct or another channel.
              Google AI Overview clicks cannot currently be separated from ordinary
              Google organic search by this integration, so they are counted as
              organic search rather than guessed as AI. These are session counts,
              not a complete measure of AI influence.
            </p>
          </Cell>
        </>
      )}

      {status?.status === "not_connected" && !canManage && (
        <Cell className="sp-ga4-card">
          <p>Google Analytics isn't connected yet. Ask an organization owner or admin to connect it.</p>
        </Cell>
      )}
    </Shell>

    {showConsent && (
      <GoogleConsentModal
        onCancel={closeConsent} onConfirm={beginOAuth} busy={busy}
        error={consentError} onRetry={beginOAuth}
      />
    )}
    </>
  );
}

function SectionError({ title, message }) {
  return (
    <Cell className="sp-ga4-card">
      <h2>{title}</h2>
      <p className="sp-ga4-section-error"><AlertTriangle size={14} /> {message}</p>
    </Cell>
  );
}

/* ============================= Executive Overview ============================= */

const OVERVIEW_CARDS = [
  { key: "total_users", label: "Total users", fmt: fmtInt, tip: "GA4 totalUsers: distinct users who had at least one session in this period." },
  { key: "new_users", label: "New users", fmt: fmtInt, tip: "GA4 newUsers: users whose first-ever session fell in this period." },
  { key: "returning_users", label: "Returning users", fmt: fmtInt, tip: "Derived: total users minus new users for this exact period — not a raw GA4 metric." },
  { key: "sessions", label: "Sessions", fmt: fmtInt, tip: "GA4 sessions." },
  { key: "views", label: "Views", fmt: fmtInt, tip: "GA4 screenPageViews." },
  { key: "engaged_sessions", label: "Engaged sessions", fmt: fmtInt, tip: "GA4 engagedSessions: sessions lasting 10s+, with a key event, or 2+ pageviews." },
  { key: "engagement_rate", label: "Engagement rate", fmt: fmtPct, tip: "Derived: engaged sessions ÷ sessions." },
  { key: "avg_engagement_time_sec", label: "Avg. engagement time", fmt: fmtSecs, tip: "Derived: total engagement time ÷ sessions." },
  { key: "key_events", label: "Key events", fmt: fmtInt, tip: "GA4 keyEvents — configured conversions for this property." },
];

function OverviewCards({ overview, compareEnabled, loading }) {
  if (!overview) return null;
  const { current, previous, key_events_configured, unavailable_reason } = overview;
  if (unavailable_reason || !current) {
    return (
      <Cell className="sp-ga4-card">
        <h2>Executive overview</h2>
        <p className="sp-ga4-empty-inline">Overview totals aren't available right now — your connection isn't fully active.</p>
      </Cell>
    );
  }
  return (
    <Cell className="sp-ga4-card">
      <h2>Executive overview {loading && <span className="sp-ga4-refreshing">refreshing…</span>}</h2>
      <div className="sp-ga4-kpis">
        {OVERVIEW_CARDS.map((c) => {
          const isKeyEvents = c.key === "key_events";
          const unconfigured = isKeyEvents && key_events_configured === false;
          const value = current[c.key];
          const delta = compareEnabled && previous ? fmtDelta(value, previous[c.key]) : null;
          return (
            <div key={c.key} className="sp-ga4-kpi" title={c.tip}>
              <div className="sp-ga4-kpi-label">{c.label}</div>
              {unconfigured ? (
                <div className="sp-ga4-kpi-unconfigured">Not configured in GA4</div>
              ) : (
                <>
                  <div className="sp-ga4-kpi-value">{c.fmt(value)}</div>
                  {delta && (
                    <div className={`sp-ga4-kpi-delta ${delta.dir}`}>
                      {delta.dir === "up" ? <ChevronUp size={12} /> : <ChevronDown size={12} />} {delta.text}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
      {key_events_configured === false && (
        <p className="sp-ga4-empty-inline">
          No key events are configured on this GA4 property yet — conversion
          numbers above and in the tables below will read "Not configured" until
          you set one up in Google Analytics (Admin → Events → Mark as key event).
        </p>
      )}
    </Cell>
  );
}

/* ================================ Traffic Trends =============================== */

const TREND_VIEWS = [
  { key: "users", label: "Users" },
  { key: "sessions", label: "Sessions & engagement" },
  { key: "ai", label: "AI referral sessions" },
];
const AI_COLORS = {
  chatgpt: "#10B981", perplexity: "#3B82F6", gemini: "#8B5CF6", copilot: "#06B6D4",
  claude: "#F59E0B", other_identifiable: "#6B7280", unattributable: "#D1D5DB",
};

function TrendsSection({ trends }) {
  const [view, setView] = useState("users");
  if (!trends) return null;
  const hasData = trends.daily.length > 0;
  const data = hasData ? trends.daily : [{ date: trends.date_range.start }, { date: trends.date_range.end }];

  return (
    <Cell className="sp-ga4-card">
      <h2>Traffic trends</h2>
      {trends.synced === false && (
        <p className="sp-ga4-helper">The daily overview report hasn't synced successfully yet; it retries on the next sync.</p>
      )}
      <div className="sp-ga4-trend-tabs">
        {TREND_VIEWS.map((v) => (
          <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>{v.label}</button>
        ))}
      </div>
      <div className="sp-ga4-chart">
        <ResponsiveContainer width="100%" height="100%">
          {view === "users" ? (
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--sp-line-2)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
              <RTooltip />
              <Area type="monotone" dataKey="total_users" name="Total users" stroke="#3B82F6" fill="#3B82F6" fillOpacity={0.15} />
              <Area type="monotone" dataKey="new_users" name="New users" stroke="#10B981" fill="#10B981" fillOpacity={0.15} />
            </AreaChart>
          ) : view === "sessions" ? (
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--sp-line-2)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
              <RTooltip />
              <Area type="monotone" dataKey="sessions" name="Sessions" stroke="#3B82F6" fill="#3B82F6" fillOpacity={0.15} />
              <Area type="monotone" dataKey="engaged_sessions" name="Engaged sessions" stroke="#10B981" fill="#10B981" fillOpacity={0.15} />
            </AreaChart>
          ) : (
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--sp-line-2)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
              <RTooltip />
              {[...AI_ENGINE_ORDER, ...AI_OTHER_ORDER].map((label) => (
                <Area key={label} type="monotone" dataKey={(d) => d.ai_sessions?.[label] ?? 0}
                      name={AI_LABELS[label]} stackId="ai" stroke={AI_COLORS[label]} fill={AI_COLORS[label]} fillOpacity={0.5} />
              ))}
            </AreaChart>
          )}
        </ResponsiveContainer>
        {!hasData && <p className="sp-ga4-chart-caption">No traffic recorded for this period yet.</p>}
      </div>
    </Cell>
  );
}

/* ============================= Acquisition Channels ============================= */

function SortHeader({ label, field, sort, setSort }) {
  const active = sort.replace("-", "") === field;
  const desc = sort.startsWith("-");
  // Selecting a new column defaults to descending (highest value first, the more
  // useful default for a metrics table); clicking the already-active column toggles.
  return (
    <th className="sp-ga4-sortable" onClick={() => setSort(active && desc ? field : `-${field}`)}>
      {label}{active && (desc ? <ChevronDown size={11} /> : <ChevronUp size={11} />)}
    </th>
  );
}

function ChannelsTable({ channels, sort, setSort, query, setQuery, embedded = false }) {
  if (!channels) return null;
  const rows = channels.channels;
  // Embedded inside "Traffic source comparison" as its progressive-disclosure detail.
  const Wrap = embedded ? "div" : Cell;
  return (
    <Wrap className={embedded ? "sp-ga4-embedded" : "sp-ga4-card"}>
      {!embedded && <h2>Acquisition channels</h2>}
      <div className="sp-ga4-table-toolbar">
        <Search size={13} />
        <input placeholder="Search channel, source, or medium…" value={query}
               onChange={(e) => setQuery(e.target.value)} />
      </div>
      {rows.length === 0 ? (
        <p className="sp-ga4-empty-inline">No channel data recorded for this period yet.</p>
      ) : (
        <div className="sp-ga4-table-wrap">
          <table className="sp-ga4-table">
            <thead>
              <tr>
                <SortHeader label="Channel" field="channel" sort={sort} setSort={setSort} />
                <th>Source / medium</th>
                <SortHeader label="New users" field="new_users" sort={sort} setSort={setSort} />
                <SortHeader label="Sessions" field="sessions" sort={sort} setSort={setSort} />
                <SortHeader label="Engaged" field="engaged_sessions" sort={sort} setSort={setSort} />
                <SortHeader label="Eng. rate" field="engagement_rate" sort={sort} setSort={setSort} />
                <SortHeader label="Views" field="views" sort={sort} setSort={setSort} />
                <SortHeader label="Key events" field="key_events" sort={sort} setSort={setSort} />
                <SortHeader label="Conv. rate" field="conversion_rate" sort={sort} setSort={setSort} />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={AI_ENGINE_ORDER.includes(r.ai_source_label) ? "ai" : ""}>
                  <td>{r.channel}</td>
                  <td className="sp-ga4-dim">{r.source} / {r.medium}</td>
                  <td>{fmtInt(r.new_users)}</td>
                  <td>{fmtInt(r.sessions)}</td>
                  <td>{fmtInt(r.engaged_sessions)}</td>
                  <td>{fmtPct(r.engagement_rate)}</td>
                  <td>{fmtInt(r.views)}</td>
                  <td>{fmtInt(r.key_events)}</td>
                  <td>{fmtPct(r.conversion_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="sp-ga4-empty-inline sp-ga4-caveat">
        Users aren't shown per channel — GA4 user counts aren't additive across
        channels, so a channel-level total would double-count visitors who used more
        than one channel. See the Overview above for your real total user count.
      </p>
    </Wrap>
  );
}

/* ============================= Landing Pages ============================= */

function LandingPagesTable({ landingPages, sort, setSort, query, setQuery }) {
  const [expanded, setExpanded] = useState(null);
  if (!landingPages) return null;
  const rows = landingPages.pages;
  return (
    <Cell className="sp-ga4-card">
      <h2>Top landing pages</h2>
      {landingPages.synced === false && (
        <p className="sp-ga4-helper">The landing-page report hasn't synced successfully yet; it retries on the next sync.</p>
      )}
      {landingPages.synced !== false && landingPages.previous_comparable === false && (
        <p className="sp-ga4-helper">Synced history doesn't cover the previous period yet, so changes aren't shown.</p>
      )}
      <div className="sp-ga4-table-toolbar">
        <Search size={13} />
        <input placeholder="Search a page path…" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {rows.length === 0 ? (
        <p className="sp-ga4-empty-inline">Not enough landing-page data recorded for this period yet.</p>
      ) : (
        <div className="sp-ga4-table-wrap">
          <table className="sp-ga4-table">
            <thead>
              <tr>
                <SortHeader label="Page" field="landing_page" sort={sort} setSort={setSort} />
                <SortHeader label="Sessions" field="sessions" sort={sort} setSort={setSort} />
                <th title="Sessions referred by a recognised AI assistant">AI</th>
                <th title="Organic search sessions (includes any Google AI Overviews clicks)">Organic</th>
                <th>vs. previous</th>
                <SortHeader label="Engaged" field="engaged_sessions" sort={sort} setSort={setSort} />
                <SortHeader label="Eng. rate" field="engagement_rate" sort={sort} setSort={setSort} />
                <SortHeader label="Key events" field="key_events" sort={sort} setSort={setSort} />
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                // null = no comparable previous value — shown as "—", never as "no change".
                const delta = p.sessions_change == null ? null : fmtDelta(p.sessions, p.sessions - p.sessions_change);
                const isOpen = expanded === p.landing_page;
                const weakEngagement = p.sessions >= 20 && p.engagement_rate != null && p.engagement_rate < 0.3;
                return (
                  <React.Fragment key={p.landing_page}>
                    <tr className={weakEngagement ? "pri-high" : ""}>
                      <td className="sp-ga4-dim">{p.landing_page}</td>
                      <td>{fmtInt(p.sessions)}</td>
                      <td>{fmtInt(p.ai_sessions)}</td>
                      <td>{fmtInt(p.organic_sessions)}</td>
                      <td>{delta ? <span className={delta.dir}>{delta.text}</span> : "—"}</td>
                      <td>{fmtInt(p.engaged_sessions)}</td>
                      <td>{fmtPct(p.engagement_rate)}</td>
                      <td>{fmtInt(p.key_events)}</td>
                      <td>
                        <button type="button" className="sp-ga4-expand"
                                onClick={() => setExpanded(isOpen ? null : p.landing_page)}>
                          {isOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="sp-ga4-detail-row">
                        <td colSpan={9}>
                          {weakEngagement && (
                            <p className="sp-ga4-page-insight">
                              This page received {fmtInt(p.sessions)} sessions this period but
                              only {fmtPct(p.engagement_rate)} engagement — worth investigating
                              whether the content matches what's driving visitors here.
                            </p>
                          )}
                          {p.scan ? (
                            <a className="sp-ga4-page-link" href={`/app/scans/${p.scan.scan_id}`}>
                              View this page's AEO audit (score {p.scan.score}) <ArrowRight size={12} />
                            </a>
                          ) : (
                            <p className="sp-ga4-empty-inline">
                              No matching AEOMirror scan found for this page yet.
                            </p>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Cell>
  );
}
