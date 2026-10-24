/* Billing page (Phase 9): current plan, usage meter, plan comparison + upgrade,
   subscription management (cancel/resume), invoices and payment history. */
import React, { useCallback, useEffect, useState } from "react";
import {
  Check, Zap, Crown, RefreshCw, AlertTriangle, CreditCard, FileText, Gauge, Sparkles,
} from "lucide-react";
import { billing, startCheckout, ScanError } from "../api.js";
import { fmtDate } from "./ui.jsx";
import { Shell, SpectrumSkeletonPage, SpectrumError } from "../shared/spectrum.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";

const money = (cents, cur = "usd") => cents === 0 ? "Free" : `$${(cents / 100).toFixed(0)}`;
const PLAN_ICON = { free: Gauge, report: FileText, pro: Crown };

/* A single usage meter (used/limit + bar), primary blue → attention lav (>=80%) →
   needs-work violet (100%) — no red/amber/green anywhere. */
function Meter({ label, q }) {
  if (!q) return null;
  if (q.unlimited) {
    return (
      <div className="sp-bill-meter-top"><span className="sp-bill-meter-lbl">{label}</span>
        <span className="sp-bill-meter-n">Unlimited</span></div>
    );
  }
  const pct = q.limit ? Math.min(100, Math.round((q.used / q.limit) * 100)) : 0;
  const color = pct >= 100 ? "var(--sp-violet-d)" : pct >= 80 ? "var(--sp-lav-d)" : "var(--sp-primary)";
  return (
    <div>
      <div className="sp-bill-meter-top">
        <span className="sp-bill-meter-lbl">{label}</span>
        <span className="sp-bill-meter-n">{q.used}/{q.limit}</span>
      </div>
      <div className="sp-bill-meter"><div style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

export default function BillingView() {
  const { refreshMe } = useAuth();
  const { reloadSubscription } = useUpgrade();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [sub, plans, invoices, payments] = await Promise.all([
        billing.subscription(), billing.plans(), billing.invoices(), billing.payments(),
      ]);
      setData({ sub, plans: plans.plans, invoices: invoices.invoices, payments: payments.payments });
    } catch (e) {
      setError(e instanceof ScanError ? e.message : "Could not load billing.");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const upgrade = async () => {
    setBusy(true); setNotice(null);
    try {
      const r = await startCheckout("pro");
      if (r.completed) { setNotice("🎉 You're now on Pro!"); await load(); await refreshMe?.(); reloadSubscription?.(); }
    } catch (e) { setNotice(e instanceof ScanError ? e.message : "Checkout failed."); }
    finally { setBusy(false); }
  };
  const manage = async (fn, msg) => {
    setBusy(true); setNotice(null);
    try { await fn(); setNotice(msg); await load(); }
    catch (e) { setNotice(e instanceof ScanError ? e.message : "Action failed."); }
    finally { setBusy(false); }
  };

  if (error) return <SpectrumError message={error} onRetry={load} />;
  if (!data) return <SpectrumSkeletonPage />;

  const { sub, plans, invoices, payments } = data;
  const plan = sub.plan;
  const usageAll = sub.usage || {};
  const subscription = sub.subscription;
  const isPro = plan === "pro";

  return (
    <div className="spectrum-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1 }}>
        {notice && <div className="sp-bill-notice">{notice}</div>}

        {/* current plan + usage */}
        <div className="sp-grid2" style={{ marginBottom: 16 }}>
          <div className="sp-panel">
            <div className="sp-bill-stat-l"><Crown size={13} /> Current plan</div>
            <div className="sp-bill-plan-name">{isPro ? "Pro" : "Free"}</div>
            {subscription && (
              <div className="sp-dim" style={{ fontSize: 12.5 }}>
                {subscription.cancel_at_period_end
                  ? <>Cancels on {fmtDate(subscription.current_period_end)}</>
                  : subscription.status === "active"
                    ? <>Renews {fmtDate(subscription.current_period_end)}</>
                    : <>Status: {subscription.status}</>}
              </div>
            )}
            {isPro && subscription && (
              <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
                {subscription.cancel_at_period_end
                  ? <button className="sp-btn sp-gradient sp-sm" disabled={busy} onClick={() => manage(billing.resume, "Subscription resumed.")}><RefreshCw size={13} /> Resume</button>
                  : <button className="sp-btn sp-ghost sp-sm" disabled={busy} onClick={() => manage(billing.cancel, "Subscription will cancel at period end.")}>Cancel subscription</button>}
              </div>
            )}
          </div>
          <div className="sp-panel">
            <div className="sp-bill-stat-l"><Gauge size={13} /> Usage this month</div>
            {isPro ? (
              <div className="sp-bill-usage-unlim">Unlimited <span className="sp-dim">· Pro plan</span></div>
            ) : (
              <div className="sp-bill-meters">
                <Meter label="Scans" q={usageAll.scans} />
                <Meter label="Monitors" q={usageAll.monitors} />
                <Meter label="Comparisons" q={usageAll.compares} />
              </div>
            )}
          </div>
        </div>

        {/* plans */}
        <div className="sp-panel-h" style={{ margin: "6px 0 12px" }}>Plans</div>
        <div className="sp-bill-plans">
          {plans.map((p) => {
            const Icon = PLAN_ICON[p.code] || Zap;
            const isCurrent = (p.code === "pro" && isPro) || (p.code === "free" && !isPro);
            return (
              <div key={p.code} className={`sp-bill-card ${p.code === "pro" ? "sp-featured" : ""}`}>
                <div className="sp-bill-card-h"><Icon size={16} /> {p.name}</div>
                <div className="sp-bill-price">{money(p.price_cents)}<span className="sp-dim">{p.interval === "month" ? "/mo" : p.interval === "one_time" ? " once" : ""}</span></div>
                <div className="sp-dim" style={{ fontSize: 12, marginBottom: 12 }}>{p.description}</div>
                {p.code === "pro" && (
                  <div className="sp-bill-hero">
                    <div className="sp-bill-hero-l"><Zap size={13} /> 15 scan jobs — up to 750 pages/month</div>
                    <div className="sp-bill-hero-l"><Sparkles size={13} /> AI-written reports &amp; content insights</div>
                  </div>
                )}
                <ul className="sp-bill-feats">{(p.features || []).map((f, i) => <li key={i}><Check size={12} /> {f}</li>)}</ul>
                {p.code === "pro" && (isCurrent
                  ? <div className="sp-bill-current-tag">Your plan</div>
                  : <button className="sp-btn sp-gradient sp-block" disabled={busy} onClick={upgrade}>{busy ? "Processing…" : "Upgrade to Pro"}</button>)}
                {p.code === "free" && isCurrent && <div className="sp-bill-current-tag">Your plan</div>}
                {p.code === "report" && <div className="sp-dim" style={{ fontSize: 11.5 }}>Purchased per report from any scan's report page.</div>}
              </div>
            );
          })}
        </div>

        {/* invoices */}
        <div className="sp-panel" style={{ marginTop: 16 }}>
          <div className="sp-panel-h"><FileText size={14} /> Invoices <span className="sp-sub">{invoices.length}</span></div>
          {invoices.length === 0 ? <div className="sp-dim" style={{ fontSize: 12.5 }}>No invoices yet.</div> : (
            <table className="sp-bill-table"><thead><tr><th>Number</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
              <tbody>{invoices.map((i) => (
                <tr key={i.id}><td className="sp-mono">{i.number}</td><td>${(i.amount_cents / 100).toFixed(2)}</td><td>{i.status}</td><td className="sp-dim">{fmtDate(i.issued_at || i.created_at)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>

        {/* payment history */}
        <div className="sp-panel" style={{ marginTop: 16 }}>
          <div className="sp-panel-h"><CreditCard size={14} /> Payment history <span className="sp-sub">{payments.length}</span></div>
          {payments.length === 0 ? <div className="sp-dim" style={{ fontSize: 12.5 }}>No payments yet.</div> : (
            <table className="sp-bill-table"><thead><tr><th>Description</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
              <tbody>{payments.map((p) => (
                <tr key={p.id}><td>{p.description || p.kind}</td><td>${(p.amount_cents / 100).toFixed(2)}</td>
                  <td><span className={`sp-bill-pay ${p.status}`}>{p.status}</span></td><td className="sp-dim">{fmtDate(p.created_at)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </div>
    </Shell></div>
  );
}
