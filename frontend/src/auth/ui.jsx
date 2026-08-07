// Shared auth UI primitives (Phase 5): centered shell, fields, alerts, buttons.
import React, { useState } from "react";
import { Radar, Eye, EyeOff, AlertTriangle, CheckCircle2, Info, Loader2 } from "lucide-react";

export function AuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="auth-brand"><Radar size={18} /> AEOMirror</div>
        <div className="auth-h">{title}</div>
        {subtitle && <div className="auth-sub">{subtitle}</div>}
        {children}
        {footer && <div className="auth-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="f-field">
      {label && <span className="f-label">{label}</span>}
      {children}
      {hint && <span className="f-hint">{hint}</span>}
    </label>
  );
}

export function TextInput(props) {
  return <input className="f-input" {...props} />;
}

export function PasswordInput({ value, onChange, placeholder = "••••••••", ...rest }) {
  const [show, setShow] = useState(false);
  return (
    <div className="f-input-wrap">
      <input className="f-input" type={show ? "text" : "password"} value={value}
             onChange={onChange} placeholder={placeholder} {...rest} />
      <button type="button" className="f-eye" tabIndex={-1}
              onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

export function Alert({ kind = "error", children }) {
  const Icon = kind === "ok" ? CheckCircle2 : kind === "info" ? Info : AlertTriangle;
  return <div className={`alert alert-${kind === "ok" ? "ok" : kind === "info" ? "info" : "error"}`}>
    <Icon size={15} /> <span>{children}</span>
  </div>;
}

export function SubmitButton({ busy, children, ...rest }) {
  return (
    <button type="submit" className="btn btn-primary btn-block" disabled={busy} {...rest}>
      {busy ? <><Loader2 size={16} className="spin-slow" /> Please wait…</> : children}
    </button>
  );
}

// 0..4 strength estimate + hint text mirroring the backend policy.
export function passwordStrength(pw) {
  if (!pw) return { score: 0, ok: false, msg: "At least 8 characters, with a letter and a number." };
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) score++;
  if (pw.length >= 12) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  const ok = pw.length >= 8 && /[A-Za-z]/.test(pw) && /\d/.test(pw);
  const msg = ok ? "Looks good." : "At least 8 characters, with a letter and a number.";
  return { score, ok, msg };
}

export function PasswordStrength({ value }) {
  const { score, msg } = passwordStrength(value);
  const colors = ["var(--bad)", "var(--bad)", "var(--warn)", "var(--good)", "var(--good)"];
  return (
    <div>
      <div className="pw-bars">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} style={{ background: i < score ? colors[score] : "var(--line-2)" }} />
        ))}
      </div>
      <div className="f-hint" style={{ marginTop: 4 }}>{msg}</div>
    </div>
  );
}

export function Avatar({ user, size }) {
  const s = size ? { width: size, height: size, fontSize: Math.round(size * 0.4) } : undefined;
  if (user?.avatar) return <img className="acct-avatar" src={user.avatar} alt="" style={s} />;
  const initial = (user?.name || user?.email || "?").trim().charAt(0).toUpperCase();
  return <div className="acct-avatar" style={s}>{initial}</div>;
}

export function RoleBadge({ role }) {
  const cls = role === "owner" ? "role-owner" : role === "admin" ? "role-admin" : "";
  return <span className={`role-badge ${cls}`}>{role}</span>;
}
