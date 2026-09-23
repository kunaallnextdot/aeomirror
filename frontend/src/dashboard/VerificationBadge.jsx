/* A tiny, shared, compact verification indicator — reused by ActionCenter, ReportView
   and Scan Details wherever a PERSISTED verification result needs a one-glance status
   next to a problem/fix, without repeating the full before/after panel (that stays in
   Scan Details — see FixVerificationPanel). Status is never color-only (icon + text
   label together). Renders NOTHING when no record is given — "no verification yet" is
   a caller-side UI fallback, never a value this component invents. */
import React from "react";
import { CheckCircle2, AlertTriangle, MinusCircle, XCircle } from "lucide-react";

export const VERIFICATION_BADGE_COPY = {
  verified: { label: "Verified", icon: CheckCircle2, tone: "ok" },
  partially_improved: { label: "Partially improved", icon: AlertTriangle, tone: "warn" },
  unchanged: { label: "Unchanged", icon: MinusCircle, tone: "info" },
  regressed: { label: "Regressed", icon: XCircle, tone: "bad" },
};

export default function VerificationBadge({ verification }) {
  if (!verification) return null;
  const copy = VERIFICATION_BADGE_COPY[verification.verification_status];
  if (!copy) return null;
  const Icon = copy.icon;
  return (
    <span className={`au-verify-badge au-verify-${copy.tone}`}>
      <Icon size={11} /> {copy.label}
    </span>
  );
}
