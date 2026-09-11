/* Upgrade / paywall UX (shared).
 *
 * - <UpgradeProvider> fetches GET /billing/subscription once and shares the plan +
 *   usage ({scans, monitors, compares}, each {limit, used, remaining, unlimited}).
 * - useUpgrade() exposes the usage, whether the org is limited (Free + enforcement),
 *   openUpgrade(reason) to pop the modal, handleGated(err, reason) to turn a backend
 *   402 into the modal, and reloadSubscription() to refresh the meters after an action.
 * - <UpgradeModal> is the dark, reason-specific paywall. Checkout goes through the
 *   same billing API BillingView uses (startCheckout).
 */
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Crown, FileText, X, AlertTriangle, Check, Sparkles } from "lucide-react";
import { billing, startCheckout, ScanError } from "../api.js";
import { useAuth } from "../auth/AuthContext.jsx";

// reason -> headline. The server's 402 detail (when present) becomes the subhead.
const COPY = {
  scans:    { title: "You've used your Free scan this month",  sub: "Upgrade to Pro for 15 scan jobs every month." },
  monitors: { title: "You've reached your monitor limit",      sub: "Upgrade to Pro for 10 monitors with alerts." },
  compares: { title: "You've used your Free comparison",       sub: "Upgrade to Pro for unlimited scan comparisons." },
  report:   { title: "Unlock the full report",                 sub: "Every recommendation and fix, the AI-written narrative, and PDF / CSV / JSON exports." },
  page_details: { title: "See every page's full breakdown",    sub: "See the full signal breakdown for every page. Pro unlocks detailed reports for up to 50 URLs per bulk scan." },
  ai_content: { title: "AI Content Insights is a Pro feature",  sub: "AI-written tone, clarity and structure analysis with specific rewrite suggestions for every page." },
  share:    { title: "You've reached your share-link limit",    sub: "Free includes 3 active public share links. Revoke one, or upgrade to Pro for unlimited." },
};

const PRO_FEATURES = [
  "15 scan jobs / month (single page or bulk-50)",
  "AI-written reports & content insights",
  "Full per-page detail on bulk scans",
  "10 monitors · unlimited comparisons",
  "PDF / CSV / JSON exports",
  "Team members & roles",
];

const DEFAULT = {
  subscription: null, usage: null, plan: null, isLimited: false,
  openUpgrade: () => {}, handleGated: () => false, reloadSubscription: async () => {},
};
const UpgradeCtx = createContext(DEFAULT);

export function useUpgrade() {
  return useContext(UpgradeCtx);
}

/** Turn a caught error into the upgrade modal when it's a billing 402.
 *  Returns true if it handled it (so the caller can skip its normal error UI). */
export function handleGated(err, reason, openUpgrade, extra = {}) {
  if (err instanceof ScanError && err.code === 402) {
    openUpgrade(reason, { ...extra, message: extra.message || err.message });
    return true;
  }
  return false;
}

export function UpgradeProvider({ children }) {
  const { refreshMe } = useAuth();
  const [subscription, setSubscription] = useState(null);
  const [modal, setModal] = useState(null);   // { reason, message, scanId, onSuccess }

  const reloadSubscription = useCallback(async () => {
    try { setSubscription(await billing.subscription()); }
    catch { /* meters are best-effort; never break the dashboard */ }
  }, []);
  useEffect(() => { reloadSubscription(); }, [reloadSubscription]);

  const openUpgrade = useCallback((reason, extra = {}) => {
    setModal({ reason, message: extra.message || null, scanId: extra.scanId || null,
               onSuccess: extra.onSuccess || null });
  }, []);
  const close = useCallback(() => setModal(null), []);

  const boundHandleGated = useCallback(
    (err, reason, extra = {}) => handleGated(err, reason, openUpgrade, extra),
    [openUpgrade]);

  const usage = subscription?.usage || null;
  const plan = subscription?.plan || null;
  // "Limited" = the FREE plan is in effect with enforcement on (report blur, locked
  // features, upgrade prompts). Pro is NOT limited even though it now has metered
  // scan/monitor caps — Pro keeps full features. When billing is off (dev) quotas
  // report unlimited, so nothing is limited.
  const isLimited = plan !== "pro" && !!usage?.scans && usage.scans.unlimited === false;
  // Whether to show a finite scan meter at all (Free = 1, Pro = 15; hidden only when
  // truly unlimited, e.g. billing off).
  const meteredScans = !!usage?.scans && usage.scans.unlimited === false;

  const value = { subscription, usage, plan, isLimited, meteredScans,
                  openUpgrade, handleGated: boundHandleGated, reloadSubscription };

  return (
    <UpgradeCtx.Provider value={value}>
      {children}
      {modal && (
        <UpgradeModal
          {...modal}
          onClose={close}
          onUpgraded={async () => { await reloadSubscription(); await refreshMe?.(); }}
        />
      )}
    </UpgradeCtx.Provider>
  );
}

function UpgradeModal({ reason, message, scanId, onSuccess, onClose, onUpgraded }) {
  const [busy, setBusy] = useState(null);   // "pro" | "report"
  const [err, setErr] = useState(null);
  const copy = COPY[reason] || COPY.scans;
  const showOneTime = reason === "report" && !!scanId;

  const go = async (which) => {
    setBusy(which); setErr(null);
    try {
      const r = which === "report"
        ? await startCheckout("report", scanId)
        : await startCheckout("pro");
      if (r.completed) {          // dev/test flow completes inline
        await onUpgraded?.();
        onSuccess?.();
        onClose();
      }
      // production: startCheckout redirected the browser to the hosted checkout.
    } catch (e) {
      setErr(e instanceof ScanError ? e.message : "Checkout failed. Please try again.");
    } finally { setBusy(null); }
  };

  return (
    <div className="au-up-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="au-up-modal" onClick={(e) => e.stopPropagation()}>
        <button className="au-up-x" onClick={onClose} aria-label="Close"><X size={16} /></button>
        <div className="au-up-eyebrow"><Sparkles size={13} /> UPGRADE</div>
        <h2 className="au-up-title">{copy.title}</h2>
        <p className="au-up-sub">{message || copy.sub}</p>

        <div className={`au-up-options${showOneTime ? " au-two" : ""}`}>
          <div className="au-up-card au-featured">
            <div className="au-up-card-h"><Crown size={15} /> Pro</div>
            <div className="au-up-price">$29<span>/mo</span></div>
            <div className="au-up-card-tag">The full toolkit</div>
            <ul className="au-up-feats">
              {PRO_FEATURES.map((f) => <li key={f}><Check size={12} /> {f}</li>)}
            </ul>
            <button className="au-btn au-accent au-block" disabled={!!busy} onClick={() => go("pro")}>
              {busy === "pro" ? "Processing…" : "Upgrade to Pro"}
            </button>
          </div>

          {showOneTime && (
            <div className="au-up-card">
              <div className="au-up-card-h"><FileText size={15} /> One-time report</div>
              <div className="au-up-price">$9<span> once</span></div>
              <div className="au-up-card-tag">Unlock just this report</div>
              <ul className="au-up-feats">
                <li><Check size={12} /> Every recommendation &amp; fix</li>
                <li><Check size={12} /> AI-written report narrative</li>
                <li><Check size={12} /> PDF / CSV / JSON exports</li>
                <li><Check size={12} /> Doesn't change your plan</li>
              </ul>
              <button className="au-btn au-ghost au-block" disabled={!!busy} onClick={() => go("report")}>
                {busy === "report" ? "Processing…" : "Unlock for $9"}
              </button>
            </div>
          )}
        </div>

        {err && <div className="au-up-err"><AlertTriangle size={13} /> {err}</div>}
        <button className="au-up-later" onClick={onClose}>Maybe later</button>
      </div>
    </div>
  );
}
