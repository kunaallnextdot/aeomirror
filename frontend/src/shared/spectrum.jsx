/* Spectrum design-system PRIMITIVES (migration Phase 3).
   Reusable components built on ONLY the Phase-1 `--sp-*` tokens (via spectrum.css) and the
   Phase-2 <Logo>. NOT wired into any real screen yet — consumed only by the dev-only
   /ui-kit-spectrum route until each screen's migration phase opts in. Every class is
   `sp-`-namespaced so nothing here can collide with the live app or with Aurora's `au-*`
   equivalents in src/dashboard/aurora.jsx (which stays untouched and keeps working).

   Engine colours are FIXED across the whole app, drawn from the brand ramp only (no
   invented hex values) — each one is an exact match of an existing --sp-* ramp stop:
     ChatGPT/OpenAI = --sp-cyan-d (#0070C9) · Claude/Anthropic = --sp-violet (#6B4BE8)
     Perplexity = --sp-blue (#1B5CF5, == --sp-primary) · Gemini = --sp-indigo (#2A14DC)
   Letters reuse the app's existing convention (ChatGPT/Gemini both "G", Claude "C",
   Perplexity "P" — verbatim from AURORA_ENGINES, not reinvented). */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Check, Eye, EyeOff, CheckCircle2, Info, AlertTriangle, Loader2, Radar, RefreshCw } from "lucide-react";
import { passwordStrength } from "../auth/ui.jsx";
import Logo from "./Logo.jsx";
import "./spectrum.css";

const cx = (...c) => c.filter(Boolean).join(" ");

/* Fixed engine registry — keyed by the backend provider id AND the display name. */
export const SPECTRUM_ENGINES = {
  openai:     { name: "ChatGPT",    letter: "G", color: "var(--sp-cyan-d)" },
  chatgpt:    { name: "ChatGPT",    letter: "G", color: "var(--sp-cyan-d)" },
  anthropic:  { name: "Claude",     letter: "C", color: "var(--sp-violet)" },
  claude:     { name: "Claude",     letter: "C", color: "var(--sp-violet)" },
  perplexity: { name: "Perplexity", letter: "P", color: "var(--sp-blue)" },
  gemini:     { name: "Gemini",     letter: "G", color: "var(--sp-indigo)" },
  copilot:    { name: "Copilot",    letter: "M", color: "var(--sp-cyan)" },
};
export function spectrumEngine(key) {
  return SPECTRUM_ENGINES[String(key || "").trim().toLowerCase()] || null;
}

/* 1. Shell — 4 blurred tint blobs (decorative, pointer-events:none) behind a z-indexed
   content layer. overflow:hidden on the root itself (not left to a future per-screen
   wrapper) so the blobs can never cause horizontal scroll, wherever this mounts. */
export function Shell({ children, className, ...rest }) {
  return (
    <div className={cx("sp-shell-root", className)} {...rest}>
      <div className="sp-aurora" aria-hidden="true">
        <span className="sp-blob sp-b1" /><span className="sp-blob sp-b2" />
        <span className="sp-blob sp-b3" /><span className="sp-blob sp-b4" />
      </div>
      <div className="sp-shell">{children}</div>
    </div>
  );
}

/* 2. TopNav — glass bar. Brand is the real <Logo>, not a drawn mark. */
export function TopNav({ items = [], credits, cta, avatar, ...rest }) {
  return (
    <nav className="sp-top" {...rest}>
      <Link to="/" className="sp-brand" aria-label="Go to AEOMirror homepage"><Logo variant="lockup" size={20} /></Link>
      <div className="sp-tnav">
        {items.map((it, i) => {
          const props = { key: i, className: cx("sp-tn", it.active && "on"), onClick: it.onClick };
          return it.href
            ? <a {...props} href={it.href}>{it.label}</a>
            : <button type="button" {...props}>{it.label}</button>;
        })}
      </div>
      <div className="sp-tright">
        {credits != null && (
          <span className="sp-credit">{typeof credits === "object" && "used" in credits
            ? <>Credits <b>{credits.used}/{credits.total}</b></> : credits}</span>
        )}
        {cta && <Button variant="gradient" onClick={cta.onClick}>{cta.label}</Button>}
        {avatar && <span className="sp-avatar">{avatar}</span>}
      </div>
    </nav>
  );
}

/* 3. PageHead — mono uppercase eyebrow + 34px display h1 + muted sub + optional pulse pill. */
export function PageHead({ eyebrow, title, sub, status }) {
  return (
    <header className="sp-head">
      <div>
        {eyebrow && <span className="sp-eb">{eyebrow}</span>}
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {status && <span className="sp-pulse"><i />{status.label ?? status}</span>}
    </header>
  );
}

/* 4. Bento + Cell. tint: cyan | blue | indigo | violet | lav. */
export function Bento({ children, className, ...rest }) {
  return <div className={cx("sp-bento", className)} {...rest}>{children}</div>;
}
export function Cell({ span, solid, tint, className, children, ...rest }) {
  return (
    <div className={cx("sp-cell", span && `sp-c${span}`, solid && "sp-solid",
      tint && `sp-tint-${tint}`, className)} {...rest}>
      {children}
    </div>
  );
}

/* 5. Metric — mono uppercase label, 46px display value (optional gradient text-fill),
   mono delta chip, muted sub. */
export function Metric({ label, value, delta, sub, gradient = false }) {
  return (
    <div>
      {label && <span className="sp-lbl">{label}</span>}
      <div>
        <span className={cx("sp-big", "sp-mono-num", gradient && "sp-grad-text")}>{value}</span>
        {delta && (
          <span className={cx("sp-chg", delta.dir === "down" ? "sp-d" : "sp-u")}>
            {delta.dir === "down" ? "▼" : "▲"} {delta.value}
          </span>
        )}
      </div>
      {sub && <div className="sp-sub">{sub}</div>}
    </div>
  );
}

/* 6. Tag / Chip — variant maps to the Phase-1 semantic pairs: healthy | warning |
   needs-work | neutral (no red/amber/green anywhere). */
export function Tag({ variant = "neutral", children, className, ...rest }) {
  return <span className={cx("sp-tag", `sp-${variant}`, className)} {...rest}>{children}</span>;
}
export { Tag as Chip };

/* 7. Button — variant: ink | gradient | ghost. Forwards ALL props incl. disabled;
   `loading` shows a spinner and disables the control. */
export function Button({ variant = "ink", loading = false, disabled = false,
                         children, className, ...rest }) {
  return (
    <button className={cx("sp-btn", `sp-${variant}`, className)}
            disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading && <span className="sp-btn-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

/* 8. Ring — 62px circular progress, 8px stroke, round cap, -90deg rotate, dasharray 283,
   1.4s dashoffset transition. Colour from a threshold prop, drawn from the ramp only. */
export function spectrumRingColor(value, thresholds = { good: 75, warn: 45 }) {
  if (value >= thresholds.good) return "var(--sp-blue-d)";
  if (value >= thresholds.warn) return "var(--sp-lav-d)";
  return "var(--sp-violet-d)";
}
export function Ring({ value = 0, size = 62, stroke = 8, thresholds, color, label }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const r = (100 - stroke) / 2;
  const circumference = 283; // matches the prototype's stroke-dasharray:283 (2πr, r≈45)
  const offset = circumference * (1 - v / 100);
  const stroked = color || spectrumRingColor(v, thresholds);
  return (
    <div className="sp-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 100 100">
        <circle className="sp-ring-bg" cx="50" cy="50" r={r} strokeWidth={stroke} />
        <circle className="sp-ring-fg" cx="50" cy="50" r={r} strokeWidth={stroke}
                stroke={stroked} strokeDashoffset={offset} />
      </svg>
      <span className="sp-ring-v" style={{ fontSize: size * 0.29 }}>{label ?? v}</span>
    </div>
  );
}

/* 9. ProgressBar — 6px track, 99px radius, brand-gradient fill, .7s width transition. */
export function ProgressBar({ value = 0, className, ...rest }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={cx("sp-pg", className)} role="progressbar" aria-valuenow={v}
         aria-valuemin={0} aria-valuemax={100} {...rest}>
      <div className="sp-pgf" style={{ width: `${v}%` }} />
    </div>
  );
}

/* 10. Skeleton — shimmer, 400% background-size, 1.5s loop, blue-tinted midpoint. */
export function Skeleton({ w = "100%", h = 16, style, className }) {
  return <div className={cx("sp-skel", className)} style={{ width: w, height: h, ...style }} />;
}

/* 11. StepList — steps: [{ label, state: 'idle'|'active'|'done', time }]. Active = spinning
   ring border; done = filled primary circle with a tick. */
export function StepList({ steps = [] }) {
  return (
    <div className="sp-steps">
      {steps.map((s, i) => (
        <div key={i} className={cx("sp-st", s.state === "active" && "sp-act", s.state === "done" && "sp-done")}>
          <span className="sp-sti"><Check strokeWidth={3} /></span>
          <span className="sp-stx">{s.label}</span>
          {s.time != null && <span className="sp-stt">{s.time}</span>}
        </div>
      ))}
    </div>
  );
}

/* Three-dot typing indicator (engine in-flight). */
export function TypingDots() {
  return <span className="sp-typing" aria-label="typing"><i /><i /><i /></span>;
}

/* 12. EngineCard — 30px rounded icon tile with the engine letter, name, status pill, body
   slot, mono footer, plus the three-dot typing state when pending with no children.
   `status`: 'yes' | 'no' | 'pending' — mapped onto the no-red/amber/green semantic pairs:
   yes -> healthy, no -> needs-work (NOT a danger colour), pending -> neutral. */
export function EngineCard({ engine, status, statusLabel, footer, children }) {
  const e = spectrumEngine(engine) || { name: String(engine || "—"), letter: "•", color: "var(--sp-muted)" };
  const statusClass = status === "yes" ? "sp-yes" : status === "no" ? "sp-no" : "sp-pending";
  return (
    <div className="sp-jc">
      <div className="sp-jc-h">
        <span className="sp-jc-ic" style={{ background: e.color }}>{e.letter}</span>
        <span className="sp-jc-n">{e.name}</span>
        {(statusLabel || status) && <span className={cx("sp-jc-s", statusClass)}>{statusLabel ?? status}</span>}
      </div>
      <div className="sp-jc-q">{children ?? (status === "pending" ? <TypingDots /> : null)}</div>
      {footer && <div className="sp-jc-f">{footer}</div>}
    </div>
  );
}

/* ============================================================================
   Shared route-level states (migration Phase 7) — Spectrum equivalents of
   aurora.jsx's AuroraSkeletonPage/AuroraEmptyScans/AuroraError, consumed by
   routes.jsx's gate for the scan-list-gated screens (Dashboard/Report/Compare/
   Summary) and by any screen needing a quick loading/empty/error block. Copy for
   the empty state is verbatim from Aurora's (itself verbatim from the original
   pre-Aurora EmptyState) — not reworded, only re-skinned. Illustration icons here
   are generic (Radar/AlertTriangle/RefreshCw), not the brand mark — consistent
   with the landing-page migration's call that decorative icons != the logo. ==== */
export function SpectrumSkeletonPage() {
  return (
    <div className="spectrum-screen"><Shell>
      <div className="sp-toolbar"><Skeleton w="40%" h={30} /></div>
      <div className="sp-stack">
        <Cell solid><div style={{ display: "grid", gap: 10 }}><Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} /></div></Cell>
        <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
      </div>
    </Shell></div>
  );
}

export function SpectrumEmptyScans({ onRun }) {
  return (
    <div className="spectrum-screen"><Shell>
      <Cell solid><div className="sp-card-center">
        <div className="sp-ill"><Radar size={26} /></div>
        <div className="sp-card-t">No scans yet</div>
        <div className="sp-card-s">
          Run your first scan to see:
          <ul className="sp-empty-bullets">
            <li>visibility score</li><li>biggest problems</li><li>evidence</li><li>recommended fixes</li>
          </ul>
        </div>
        <button className="sp-btn sp-gradient" onClick={onRun}><Radar size={16} /> Run first scan</button>
      </div></Cell>
    </Shell></div>
  );
}

export function SpectrumError({ message, onRetry }) {
  return (
    <div className="spectrum-screen"><Shell>
      <Cell solid><div className="sp-card-center" role="alert">
        <div className="sp-ill sp-ill-bad"><AlertTriangle size={26} /></div>
        <div className="sp-card-s" style={{ marginBottom: onRetry ? 20 : 0 }}>{message || "Something went wrong."}</div>
        {onRetry && <button className="sp-btn sp-gradient" onClick={onRetry}><RefreshCw size={14} /> Retry</button>}
      </div></Cell>
    </Shell></div>
  );
}

/* ============================================================================
   Spectrum FORM PRIMITIVES (migration Phase 7). Spectrum equivalents of
   aurora.jsx's AuField/AuTextInput/AuPasswordInput/AuPasswordStrength/AuAlert/
   AuAvatar/AuRoleBadge/AuAuthShell/AuSubmitButton, used by the Settings cluster
   and the 6 auth pages. The pure `passwordStrength` policy is reused (imported),
   not duplicated — identical to how Aurora's own version reused it. ============ */
export function SpField({ label, hint, children }) {
  return (
    <label className="sp-field">
      {label && <span className="sp-field-l">{label}</span>}
      {children}
      {hint && <span className="sp-field-hint">{hint}</span>}
    </label>
  );
}

export function SpTextInput(props) {
  return <input className="sp-input" {...props} />;
}

export function SpPasswordInput({ value, onChange, placeholder = "••••••••", ...rest }) {
  const [show, setShow] = useState(false);
  return (
    <div className="sp-input-wrap">
      <input className="sp-input" type={show ? "text" : "password"} value={value}
             onChange={onChange} placeholder={placeholder} {...rest} />
      <button type="button" className="sp-eye" tabIndex={-1}
              onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

/* Password-strength colour: no red/amber/green — weak->violet (needs-work),
   fair->lav (attention), strong/great->blue (healthy). Same mapping the
   landing page's status colours already use. */
export function SpPasswordStrength({ value }) {
  const { score, msg } = passwordStrength(value);
  const colors = ["var(--sp-violet-d)", "var(--sp-violet-d)", "var(--sp-lav-d)", "var(--sp-blue-d)", "var(--sp-blue-d)"];
  return (
    <div>
      <div className="sp-pw-bars">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} style={{ background: i < score ? colors[score] : "var(--sp-line-2)" }} />
        ))}
      </div>
      <div className="sp-field-hint" style={{ marginTop: 4 }}>{msg}</div>
    </div>
  );
}

export function SpAlert({ kind = "error", children }) {
  const Icon = kind === "ok" ? CheckCircle2 : kind === "info" ? Info : AlertTriangle;
  return (
    <div className={`sp-alert sp-alert-${kind === "ok" ? "ok" : kind === "info" ? "info" : "error"}`}>
      <Icon size={15} /> <span>{children}</span>
    </div>
  );
}

export function SpAvatar({ user, size }) {
  const s = size ? { width: size, height: size, fontSize: Math.round(size * 0.4) } : undefined;
  if (user?.avatar) return <img className="sp-tb-avatar-img" src={user.avatar} alt="" style={s} />;
  const initial = (user?.name || user?.email || "?").trim().charAt(0).toUpperCase();
  return <div className="sp-avatar" style={s}>{initial}</div>;
}

export function SpRoleBadge({ role }) {
  const cls = role === "owner" ? "sp-role-owner" : role === "admin" ? "sp-role-admin" : "";
  return <span className={`sp-role-badge ${cls}`}>{role}</span>;
}

/* Centered auth shell + submit button — equivalents of aurora.jsx's AuAuthShell/
   AuSubmitButton (which itself replaced the original dark auth/ui.jsx pair). */
export function SpAuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="sp-auth-wrap">
      <div className="sp-auth-card">
        <Link to="/" className="sp-auth-brand" aria-label="Go to AEOMirror homepage"><Logo variant="lockup" size={18} /></Link>
        <div className="sp-auth-h">{title}</div>
        {subtitle && <div className="sp-auth-sub">{subtitle}</div>}
        {children}
        {footer && <div className="sp-auth-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function SpSubmitButton({ busy, children, ...rest }) {
  return (
    <button type="submit" className="sp-btn sp-gradient sp-block" disabled={busy} {...rest}>
      {busy ? <><Loader2 size={16} className="sp-spin-icon" /> Please wait…</> : children}
    </button>
  );
}
