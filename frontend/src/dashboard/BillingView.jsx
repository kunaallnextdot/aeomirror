/* Billing page (Phase 9): current plan, usage meter, plan comparison + upgrade,
   subscription management (cancel/resume), invoices and payment history. */
import React, { useCallback, useEffect, useState } from "react";
import {
  Check, Zap, Crown, RefreshCw, AlertTriangle, CreditCard, FileText, Gauge, Sparkles,
} from "lucide-react";
import { billing, startCheckout, ScanError } from "../api.js";
import { TableSkeleton, ErrorState, fmtDate } from "./ui.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useUpgrade } from "./UpgradeModal.jsx";

const money = (cents, cur = "usd") => cents === 0 ? "Free" : `$${(cents / 100).toFixed(0)}`;
const PLAN_ICON = { free: Gauge, report: FileText, pro: Crown };

/* A single usage meter (used/limit + bar), teal → amber (>=80%) → red (100%). */
function Meter({ label, q }) {
  if (!q) return null;
  if (q.unlimited) {
    return (
      <div className="bill-meter-row-top"><span className="bill-meter-row-lbl">{label}</span>
        <span className="bill-meter-row-n">Unlimited</span></div>
    );
  }
  const pct = q.limit ? Math.min(100, Math.round((q.used / q.limit) * 100)) : 0;
  const color = pct >= 100 ? "var(--bad)" : pct >= 80 ? "var(--warn)" : "var(--accent)";
  return (
    <div>
      <div className="bill-meter-row-top">
        <span className="bill-meter-row-lbl">{label}</span>
        <span className="bill-meter-row-n">{q.used}/{q.limit}</span>
      </div>
      <div className="bill-meter"><div style={{ width: `${pct}%`, background: color }} /></div>
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

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <TableSkeleton rows={5} />;

  const { sub, plans, invoices, payments } = data;
  const plan = sub.plan;
  const usageAll = sub.usage || {};
  const subscription = sub.subscription;
  const isPro = plan === "pro";

  return (
    <div className="bill">
      {notice && <div className="bill-notice">{notice}</div>}

      {/* current plan + usage */}
      <div className="d-grid d-grid-2" style={{ marginBottom: 16 }}>
        <div className="d-panel bill-current">
          <div className="d-stat-label"><Crown size={13} /> Current plan</div>
          <div className="bill-plan-name">{isPro ? "Pro" : "Free"}</div>
          {subscription && (
            <div className="d-dim" style={{ fontSize: 12.5 }}>
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
                ? <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => manage(billing.resume, "Subscription resumed.")}><RefreshCw size={13} /> Resume</button>
                : <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => manage(billing.cancel, "Subscription will cancel at period end.")}>Cancel subscription</button>}
            </div>
          )}
        </div>
        <div className="d-panel">
          <div className="d-stat-label"><Gauge size={13} /> Usage this month</div>
          {isPro ? (
            <div className="bill-usage-unlim">Unlimited <span className="d-dim">· Pro plan</span></div>
          ) : (
            <div className="bill-meters">
              <Meter label="Scans" q={usageAll.scans} />
              <Meter label="Monitors" q={usageAll.monitors} />
              <Meter label="Comparisons" q={usageAll.compares} />
            </div>
          )}
        </div>
      </div>

      {/* plans */}
      <div className="d-panel-h" style={{ margin: "6px 0 12px" }}>Plans</div>
      <div className="bill-plans">
        {plans.map((p) => {
          const Icon = PLAN_ICON[p.code] || Zap;
          const isCurrent = (p.code === "pro" && isPro) || (p.code === "free" && !isPro);
          return (
            <div key={p.code} className={`bill-card ${p.code === "pro" ? "featured" : ""}`}>
              <div className="bill-card-h"><Icon size={16} /> {p.name}</div>
              <div className="bill-price">{money(p.price_cents)}<span className="d-dim">{p.interval === "month" ? "/mo" : p.interval === "one_time" ? " once" : ""}</span></div>
              <div className="d-dim" style={{ fontSize: 12, marginBottom: 12 }}>{p.description}</div>
              {p.code === "pro" && (
                <div className="bill-hero">
                  <div className="bill-hero-l"><Zap size={13} /> 15 scan jobs — up to 750 pages/month</div>
                  <div className="bill-hero-l"><Sparkles size={13} /> AI-written reports &amp; content insights</div>
                </div>
              )}
              <ul className="bill-feats">{(p.features || []).map((f, i) => <li key={i}><Check size={12} /> {f}</li>)}</ul>
              {p.code === "pro" && (isCurrent
                ? <div className="bill-current-tag">Your plan</div>
                : <button className="btn btn-primary btn-block" disabled={busy} onClick={upgrade}>{busy ? "Processing…" : "Upgrade to Pro"}</button>)}
              {p.code === "free" && isCurrent && <div className="bill-current-tag">Your plan</div>}
              {p.code === "report" && <div className="d-dim" style={{ fontSize: 11.5 }}>Purchased per report from any scan's report page.</div>}
            </div>
          );
        })}
      </div>

      {/* invoices */}
      <div className="d-panel" style={{ marginTop: 16 }}>
        <div className="d-panel-h"><FileText size={14} /> Invoices <span className="sub">{invoices.length}</span></div>
        {invoices.length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>No invoices yet.</div> : (
          <table className="bill-table"><thead><tr><th>Number</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
            <tbody>{invoices.map((i) => (
              <tr key={i.id}><td className="d-mono">{i.number}</td><td>${(i.amount_cents / 100).toFixed(2)}</td><td>{i.status}</td><td className="d-dim">{fmtDate(i.issued_at || i.created_at)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>

      {/* payment history */}
      <div className="d-panel" style={{ marginTop: 16 }}>
        <div className="d-panel-h"><CreditCard size={14} /> Payment history <span className="sub">{payments.length}</span></div>
        {payments.length === 0 ? <div className="d-dim" style={{ fontSize: 12.5 }}>No payments yet.</div> : (
          <table className="bill-table"><thead><tr><th>Description</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
            <tbody>{payments.map((p) => (
              <tr key={p.id}><td>{p.description || p.kind}</td><td>${(p.amount_cents / 100).toFixed(2)}</td>
                <td><span className={`bill-pay ${p.status}`}>{p.status}</span></td><td className="d-dim">{fmtDate(p.created_at)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </div>
  );
}
