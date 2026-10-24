/* Trustworthy pre-OAuth consent step shown before redirecting to Google's own
   authorization page. This modal never simulates Google's consent screen and never
   shows a connected state itself — it only explains what's about to happen and lets
   the user back out before the real redirect. Closing/cancelling never calls the
   backend; only "Agree & Continue to Google" does. */
import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ExternalLink, Eye, ShieldCheck, X } from "lucide-react";

const SCOPE = "https://www.googleapis.com/auth/analytics.readonly";

export default function GoogleConsentModal({ onCancel, onConfirm, busy, error, onRetry }) {
  const [agreed, setAgreed] = useState(false);
  const dialogRef = useRef(null);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKeyDown = (e) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel, busy]);

  return (
    <div className="sp-gcm-overlay" onClick={() => !busy && onCancel()}>
      <div
        className="sp-gcm-modal" role="dialog" aria-modal="true"
        aria-labelledby="sp-gcm-title" aria-describedby="sp-gcm-sub"
        ref={dialogRef} tabIndex={-1} onClick={(e) => e.stopPropagation()}
      >
        <button className="sp-gcm-x" onClick={onCancel} aria-label="Close" disabled={busy}>
          <X size={16} />
        </button>

        <h2 id="sp-gcm-title" className="sp-gcm-title">Connect Google Analytics</h2>
        <p id="sp-gcm-sub" className="sp-gcm-sub">
          Understand how AEOMirror accesses your Google Analytics data to help you
          measure traffic from AI assistants.
        </p>

        <section className="sp-gcm-section">
          <h3><Check size={14} /> What you'll get</h3>
          <ul>
            <li>Discover the GA4 properties available to your connected Google account.</li>
            <li>Select the GA4 property you want AEOMirror to analyze.</li>
            <li>View inbound traffic attributed to identifiable AI assistants, where the
              available analytics data supports classification.</li>
            <li>Review AI referral traffic in your AEOMirror dashboard.</li>
          </ul>
        </section>

        <section className="sp-gcm-section">
          <h3><Eye size={14} /> What data will AEOMirror access?</h3>
          <ul>
            <li>Read-only access to Google Analytics data covered by the requested permission.</li>
            <li>Access is used for property discovery and retrieving analytics reports for
              the property you select.</li>
            <li>AEOMirror does not request permission to edit or administer your Google
              Analytics properties for this feature.</li>
            <li>Connecting does not guarantee every AI assistant referral can be
              identified — see the note on the AI Traffic page for why.</li>
          </ul>
          <div className="sp-gcm-scope">
            <code>{SCOPE}</code>
            <span>View your Google Analytics data.</span>
          </div>
        </section>

        <section className="sp-gcm-section">
          <h3><ShieldCheck size={14} /> Your control and privacy</h3>
          <ul>
            <li>You choose whether to continue — nothing is connected until you approve
              it on Google's own page.</li>
            <li>You can cancel below without connecting Google Analytics.</li>
            <li>Read our{" "}
              <a href="/privacy" target="_blank" rel="noreferrer">Privacy Policy <ExternalLink size={10} /></a>
              {" "}and{" "}
              <a href="/terms" target="_blank" rel="noreferrer">Terms of Service <ExternalLink size={10} /></a>.
            </li>
            <li>You can disconnect at any time from this page. When you do, we always
              delete the stored credential immediately — Google traffic already synced
              before you disconnect is kept, not automatically deleted.</li>
          </ul>
        </section>

        <label className="sp-gcm-checkbox">
          <input type="checkbox" checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)} disabled={busy} />
          I have read the information above and want to continue to Google.
        </label>

        {error && (
          <div className="sp-gcm-err">
            <AlertTriangle size={14} /> <span>{error}</span>
            {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
          </div>
        )}

        <div className="sp-gcm-actions">
          <button type="button" className="sp-btn sp-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button" className="sp-btn sp-gradient" onClick={onConfirm}
            disabled={busy || !agreed} aria-busy={busy || undefined}
          >
            {busy ? "Redirecting…" : "Agree & Continue to Google"}
          </button>
        </div>
      </div>
    </div>
  );
}
