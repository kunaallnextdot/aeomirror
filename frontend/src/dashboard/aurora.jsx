/* Aurora design-system PRIMITIVES (Phase 3).
   Reusable components that render the prototype's markup using ONLY Phase-1 `--au-*` tokens
   (via aurora.css). NOT wired into any screen yet — consumed only by the dev-only /ui-kit
   route until each screen's migration phase opts in. Every class is `au-`-namespaced.

   Engine colour assignment is FIXED across the whole app (from the prototype, verbatim):
     ChatGPT/OpenAI = mint-d "G" · Claude/Anthropic = peach-d "C"
     Perplexity = sky-d "P" · Gemini = lav-d "G"
   (The prototype uses the glyph "G" for BOTH ChatGPT and Gemini — copied as-is, not invented.) */
import React, { useState, useEffect } from "react";
import { Check, Eye, EyeOff, CheckCircle2, Info, AlertTriangle, Loader2 } from "lucide-react";
import { passwordStrength } from "../auth/ui.jsx";
import "./aurora.css";

const cx = (...c) => c.filter(Boolean).join(" ");

/* Fixed engine registry — keyed by the backend provider id AND the display name. */
export const AURORA_ENGINES = {
  openai:     { name: "ChatGPT",    letter: "G", color: "var(--au-mint-d)" },
  chatgpt:    { name: "ChatGPT",    letter: "G", color: "var(--au-mint-d)" },
  anthropic:  { name: "Claude",     letter: "C", color: "var(--au-peach-d)" },
  claude:     { name: "Claude",     letter: "C", color: "var(--au-peach-d)" },
  perplexity: { name: "Perplexity", letter: "P", color: "var(--au-sky-d)" },
  gemini:     { name: "Gemini",     letter: "G", color: "var(--au-lav-d)" },
};
export function auroraEngine(key) {
  return AURORA_ENGINES[String(key || "").trim().toLowerCase()] || null;
}

/* 1. Shell — decorative blob background + z-indexed content layer. */
export function Shell({ children, className, ...rest }) {
  return (
    <div className={cx("au-shell-root", className)} style={{ position: "relative" }} {...rest}>
      <div className="au-aurora" aria-hidden="true">
        <span className="au-blob au-b1" /><span className="au-blob au-b2" />
        <span className="au-blob au-b3" /><span className="au-blob au-b4" />
      </div>
      <div className="au-shell">{children}</div>
    </div>
  );
}

/* 2. TopNav — glass bar. Presentational: caller supplies items/credits/cta/avatar. */
export function TopNav({ brand = "AEOMirror", items = [], credits, cta, avatar, ...rest }) {
  return (
    <nav className="au-top" {...rest}>
      <div className="au-brand">
        <span className="au-bm"><i /></span>
        <span className="au-wordmark">{brand}</span>
      </div>
      <div className="au-tnav">
        {items.map((it, i) => {
          const props = { key: i, className: cx("au-tn", it.active && "on"), onClick: it.onClick };
          return it.href
            ? <a {...props} href={it.href}>{it.label}</a>
            : <button type="button" {...props}>{it.label}</button>;
        })}
      </div>
      <div className="au-tright">
        {credits != null && (
          <span className="au-credit">{typeof credits === "object" && "used" in credits
            ? <>Credits <b>{credits.used}/{credits.total}</b></> : credits}</span>
        )}
        {cta && <Button variant="primary" onClick={cta.onClick}>{cta.label}</Button>}
        {avatar && <span className="au-avatar">{avatar}</span>}
      </div>
    </nav>
  );
}

/* 3. PageHead — eyebrow + h1 + sub + optional status pulse pill. */
export function PageHead({ eyebrow, title, sub, status }) {
  return (
    <header className="au-head">
      <div>
        {eyebrow && <span className="au-eb">{eyebrow}</span>}
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {status && <span className="au-pulse"><i />{status.label ?? status}</span>}
    </header>
  );
}

/* 4. Bento + Cell. */
export function Bento({ children, className, ...rest }) {
  return <div className={cx("au-bento", className)} {...rest}>{children}</div>;
}
export function Cell({ span, solid, tint, className, children, ...rest }) {
  return (
    <div className={cx("au-cell", span && `au-c${span}`, solid && "au-solid",
      tint && `au-tint-${tint}`, className)} {...rest}>
      {children}
    </div>
  );
}

/* 5. Metric — label + big value + optional delta chip + sub. */
export function Metric({ label, value, delta, sub }) {
  return (
    <div>
      {label && <span className="au-lbl">{label}</span>}
      <div>
        <span className="au-big au-mono-num">{value}</span>
        {delta && (
          <span className={cx("au-chg", delta.dir === "down" ? "au-d" : "au-u")}>
            {delta.dir === "down" ? "▼" : "▲"} {delta.value}
          </span>
        )}
      </div>
      {sub && <div className="au-sub">{sub}</div>}
    </div>
  );
}

/* 6. Tag / Chip — variant: critical | warning | ok | info. */
export function Tag({ variant = "info", children, className, ...rest }) {
  return <span className={cx("au-tag", `au-${variant}`, className)} {...rest}>{children}</span>;
}
export { Tag as Chip };

/* 7. Button — variant primary|accent|ghost. Forwards ALL props incl. disabled; `loading`
   shows a spinner and disables the control. */
export function Button({ variant = "primary", loading = false, disabled = false,
                         children, className, ...rest }) {
  return (
    <button className={cx("au-btn", `au-${variant}`, className)}
            disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading && <span className="au-btn-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

/* 8. Ring — circular progress; colour driven by a threshold prop (or explicit `color`). */
export function ringColor(value, thresholds = { good: 75, warn: 45 }) {
  if (value >= thresholds.good) return "var(--au-primary)";
  if (value >= thresholds.warn) return "var(--au-lemon-d)";
  return "var(--au-peach-d)";
}
export function Ring({ value = 0, size = 62, stroke = 8, thresholds, color, label }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const r = (100 - stroke) / 2;                 // viewBox 0..100, radius inset by stroke
  const circumference = 283;                    // matches prototype stroke-dasharray:283 (2πr, r≈45)
  const offset = circumference * (1 - v / 100);
  const stroked = color || ringColor(v, thresholds);
  return (
    <div className="au-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 100 100">
        <circle className="au-ring-bg" cx="50" cy="50" r={r} strokeWidth={stroke} />
        <circle className="au-ring-fg" cx="50" cy="50" r={r} strokeWidth={stroke}
                stroke={stroked} strokeDashoffset={offset} />
      </svg>
      <span className="au-ring-v" style={{ fontSize: size * 0.29 }}>{label ?? v}</span>
    </div>
  );
}

/* 9. ProgressBar. */
export function ProgressBar({ value = 0, className, ...rest }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={cx("au-pg", className)} role="progressbar" aria-valuenow={v}
         aria-valuemin={0} aria-valuemax={100} {...rest}>
      <div className="au-pgf" style={{ width: `${v}%` }} />
    </div>
  );
}

/* 10. Skeleton. */
export function Skeleton({ w = "100%", h = 16, style, className }) {
  return <div className={cx("au-skel", className)} style={{ width: w, height: h, ...style }} />;
}

/* 11. StepList — steps: [{ label, state: 'idle'|'active'|'done', time }]. */
export function StepList({ steps = [] }) {
  return (
    <div className="au-steps">
      {steps.map((s, i) => (
        <div key={i} className={cx("au-st", s.state === "active" && "au-act", s.state === "done" && "au-done")}>
          <span className="au-sti"><Check strokeWidth={3} /></span>
          <span className="au-stx">{s.label}</span>
          {s.time != null && <span className="au-stt">{s.time}</span>}
        </div>
      ))}
    </div>
  );
}

/* Three-dot typing indicator (engine in-flight). */
export function TypingDots() {
  return <span className="au-typing" aria-label="typing"><i /><i /><i /></span>;
}

/* 12. EngineCard — engine tile + status pill + body slot + mono footer.
   `status`: 'yes' | 'no' | 'pending'. When pending and no children, shows typing dots. */
export function EngineCard({ engine, status, statusLabel, footer, children }) {
  const e = auroraEngine(engine) || { name: String(engine || "—"), letter: "•", color: "var(--au-muted)" };
  const statusClass = status === "yes" ? "au-yes" : status === "no" ? "au-no" : "au-pending";
  return (
    <div className="au-jc">
      <div className="au-jc-h">
        <span className="au-jc-ic" style={{ background: e.color }}>{e.letter}</span>
        <span className="au-jc-n">{e.name}</span>
        {(statusLabel || status) && <span className={cx("au-jc-s", statusClass)}>{statusLabel ?? status}</span>}
      </div>
      <div className="au-jc-q">{children ?? (status === "pending" ? <TypingDots /> : null)}</div>
      {footer && <div className="au-jc-f">{footer}</div>}
    </div>
  );
}

/* ============================================================================
   Shared route-level states (Aurora) for the scan-list-gated screens. Copy for the empty
   state is verbatim from the pre-Aurora EmptyState; used by AuroraGated (routes.jsx) so
   Dashboard/Report/Compare/Summary migrate without touching the shared dark <Gated>. ==== */
import { Radar as _AuRadarIcon, RefreshCw as _AuRefresh } from "lucide-react";

export function AuroraSkeletonPage() {
  return (
    <div className="aurora-screen"><Shell>
      <div className="au-toolbar"><Skeleton w="40%" h={30} /></div>
      <div className="au-stack">
        <Cell solid><div style={{ display: "grid", gap: 10 }}><Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} /></div></Cell>
        <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
      </div>
    </Shell></div>
  );
}

export function AuroraEmptyScans({ onRun }) {
  return (
    <div className="aurora-screen"><Shell>
      <Cell solid><div className="au-card-center">
        <div className="au-ill"><_AuRadarIcon size={26} /></div>
        <div className="au-card-t">No scans yet</div>
        <div className="au-card-s">
          AEOMirror checks whether AI systems like ChatGPT, Claude, Gemini and Perplexity
          can reach, read and understand any website — then scores it across 10 signals.
          Run your first scan to populate this dashboard.
        </div>
        <button className="au-btn au-accent" onClick={onRun}><_AuRadarIcon size={16} /> Run first scan</button>
      </div></Cell>
    </Shell></div>
  );
}

export function AuroraError({ message, onRetry }) {
  return (
    <div className="aurora-screen"><Shell>
      <Cell solid><div className="au-card-center" role="alert">
        <div className="au-ill au-ill-bad"><AlertTriangle size={26} /></div>
        <div className="au-card-s" style={{ marginBottom: onRetry ? 20 : 0 }}>{message || "Something went wrong."}</div>
        {onRetry && <button className="au-btn au-accent" onClick={onRetry}><_AuRefresh size={14} /> Retry</button>}
      </div></Cell>
    </Shell></div>
  );
}

/* ============================================================================
   Aurora FORM PRIMITIVES (Phase 10). Aurora-skinned equivalents of the shared dark
   auth/ui.jsx controls (Field/TextInput/PasswordInput/PasswordStrength/Alert/Avatar/
   RoleBadge), used by the Settings cluster and reused by the Auth pages in their own
   phase. The dark auth/ui.jsx is left UNTOUCHED so un-migrated auth screens keep
   rendering dark; the pure `passwordStrength` policy is reused (imported), not copied. */
export function AuField({ label, hint, children }) {
  return (
    <label className="au-field">
      {label && <span className="au-field-l">{label}</span>}
      {children}
      {hint && <span className="au-field-hint">{hint}</span>}
    </label>
  );
}

export function AuTextInput(props) {
  return <input className="au-input" {...props} />;
}

export function AuPasswordInput({ value, onChange, placeholder = "••••••••", ...rest }) {
  const [show, setShow] = useState(false);
  return (
    <div className="au-input-wrap">
      <input className="au-input" type={show ? "text" : "password"} value={value}
             onChange={onChange} placeholder={placeholder} {...rest} />
      <button type="button" className="au-eye" tabIndex={-1}
              onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

export function AuPasswordStrength({ value }) {
  const { score, msg } = passwordStrength(value);
  const colors = ["var(--au-peach-d)", "var(--au-peach-d)", "var(--au-lemon-d)", "var(--au-mint-d)", "var(--au-mint-d)"];
  return (
    <div>
      <div className="au-pw-bars">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} style={{ background: i < score ? colors[score] : "var(--au-line-2)" }} />
        ))}
      </div>
      <div className="au-field-hint" style={{ marginTop: 4 }}>{msg}</div>
    </div>
  );
}

export function AuAlert({ kind = "error", children }) {
  const Icon = kind === "ok" ? CheckCircle2 : kind === "info" ? Info : AlertTriangle;
  return (
    <div className={`au-alert au-alert-${kind === "ok" ? "ok" : kind === "info" ? "info" : "error"}`}>
      <Icon size={15} /> <span>{children}</span>
    </div>
  );
}

export function AuAvatar({ user, size }) {
  const s = size ? { width: size, height: size, fontSize: Math.round(size * 0.4) } : undefined;
  if (user?.avatar) return <img className="au-acct-avatar" src={user.avatar} alt="" style={s} />;
  const initial = (user?.name || user?.email || "?").trim().charAt(0).toUpperCase();
  return <div className="au-acct-avatar" style={s}>{initial}</div>;
}

export function AuRoleBadge({ role }) {
  const cls = role === "owner" ? "au-role-owner" : role === "admin" ? "au-role-admin" : "";
  return <span className={`au-role-badge ${cls}`}>{role}</span>;
}

/* Aurora centered auth shell + submit button (Phase 12) — equivalents of auth/ui.jsx's
   AuthShell / SubmitButton. The dark auth/ui.jsx stays (App.jsx's landing still imports its
   Avatar; #14 retires the rest). Renders on its own light ground inside the dark `.root`. */
export function AuAuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="au-auth-wrap">
      <div className="au-auth-card">
        <div className="au-auth-brand"><_AuRadarIcon size={18} /> AEOMirror</div>
        <div className="au-auth-h">{title}</div>
        {subtitle && <div className="au-auth-sub">{subtitle}</div>}
        {children}
        {footer && <div className="au-auth-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function AuSubmitButton({ busy, children, ...rest }) {
  return (
    <button type="submit" className="au-btn au-accent au-block" disabled={busy} {...rest}>
      {busy ? <><Loader2 size={16} className="au-spin" /> Please wait…</> : children}
    </button>
  );
}
