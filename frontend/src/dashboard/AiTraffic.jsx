/* AI Traffic (P0) — Google Analytics 4 connection + AI-referral traffic dashboard.
   A SEPARATE, inbound-analytics capability from Answer Tracking/Answer Simulator
   (which test how AI assistants answer queries about a site, not real visitor
   traffic). Never conflate the two in this file's copy. */
import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BarChart3, Info, Lock, RefreshCw, Unplug,
} from "lucide-react";
import {
  connectGoogleAnalytics, disconnectGoogleAnalytics, getGa4Status, getGa4Traffic,
  listGa4Properties, selectGa4Property, triggerGa4Sync, ScanError,
} from "../api.js";
import { fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";
import { Shell, Cell, Button, Skeleton } from "../shared/spectrum.jsx";
import GoogleConsentModal from "./GoogleConsentModal.jsx";
// GoogleConsentModal.jsx's au-gcm-* rules have moved into AiTraffic.spectrum.css
// (final-integration pass) — the Aurora stylesheet is no longer needed here.
import "./AiTraffic.spectrum.css";

const AI_LABELS = {
  chatgpt: "ChatGPT", perplexity: "Perplexity", gemini: "Gemini", copilot: "Copilot",
  claude: "Claude", other_identifiable: "Other identified sources", unattributable: "Unattributable",
};
const AI_ORDER = ["chatgpt", "perplexity", "gemini", "copilot", "claude", "other_identifiable", "unattributable"];

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

export default function AiTraffic() {
  const { hasPermission } = useAuth();
  const { openUpgrade } = useUpgrade();
  const canManage = hasPermission("org:update");

  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(null);
  const [traffic, setTraffic] = useState(null);
  const [properties, setProperties] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null);
  const [showConsent, setShowConsent] = useState(false);
  const [consentError, setConsentError] = useState(null);

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

  const loadTraffic = useCallback(async () => {
    try {
      setTraffic(await getGa4Traffic());
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load traffic data.");
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
      const s = await loadStatus();
      if (s?.status === "active") await loadTraffic();
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The actual OAuth kickoff — called either directly (reconnect/property-switch
  // paths, which have already been through the consent step once) or from the
  // consent modal's "Agree & Continue to Google". `busy` guards against duplicate
  // submissions (the button disables itself while true). A failure here, while the
  // modal is open, surfaces inline in the modal (with Retry) instead of behind it.
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
      setTraffic(null);
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
          Real inbound visitor traffic from your connected Google Analytics property,
          classified by AI-assistant source — separate from Answer Tracking, which tests
          how AI assistants answer queries about your site, not real visitor traffic.
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
              <div className="sp-ga4-synced">
                {status.last_synced_at
                  ? `Last synced ${fmtDate(status.last_synced_at)}`
                  : "Not synced yet"}
              </div>
            </div>
            {canManage && (
              <div className="sp-ga4-actions">
                <Button variant="ghost" onClick={syncNow} loading={busy}>
                  <RefreshCw size={14} /> Sync now
                </Button>
                <Button variant="ghost" onClick={disconnect} loading={busy}>
                  <Unplug size={14} /> Disconnect
                </Button>
              </div>
            )}
          </Cell>

          {!traffic?.synced ? (
            <Cell className="sp-ga4-card">
              <p>Sync pending — the first sync can take a few minutes after connecting.</p>
            </Cell>
          ) : (
            <Cell className="sp-ga4-card">
              <h2>Sessions by source</h2>
              <ul className="sp-ga4-sources">
                {AI_ORDER.map((key) => (
                  <li key={key}>
                    <span>{AI_LABELS[key]}</span>
                    <strong>{(traffic.by_source[key] || 0).toLocaleString()}</strong>
                  </li>
                ))}
              </ul>
            </Cell>
          )}

          <Cell className="sp-ga4-card sp-ga4-note">
            <Info size={16} />
            <p>
              Counts only traffic Google Analytics can identify by referrer. Some AI
              traffic may appear as direct or another channel. Google AI Overview clicks
              cannot currently be separated from ordinary Google organic search by this
              integration, so they stay in "Unattributable" rather than being guessed.
              These are session counts, not a complete measure of AI influence.
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
