import React, { useEffect, useState } from "react";
import { AuAuthShell, AuField, AuTextInput, AuPasswordInput, AuAlert, AuSubmitButton } from "../../dashboard/aurora.jsx";
import { useAuth } from "../AuthContext.jsx";
import { navigate } from "../router.jsx";
import { hasPendingScan } from "../pendingScan.js";

export default function Login() {
  const { login, isAuthenticated } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  // Redirect once auth state has actually committed (race-free vs. the guard). A pending
  // homepage scan returns to the scanner (auto-runs it); otherwise honour the `?next`
  // destination the auth guard preserved (internal paths only — never an open redirect),
  // falling back to the dashboard.
  useEffect(() => {
    if (!isAuthenticated) return;
    if (hasPendingScan()) { navigate("/", { replace: true }); return; }
    const next = new URLSearchParams(window.location.search).get("next");
    const safe = next && next.startsWith("/") && !next.startsWith("//") ? next : "/app";
    navigate(safe, { replace: true });
  }, [isAuthenticated]);

  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await login({ email, password, remember });
    } catch (ex) {
      setErr(ex.message || "Sign in failed.");
      setBusy(false);
    }
  };

  return (
    <AuAuthShell
      title="Welcome back"
      subtitle="Sign in to your AEOMirror dashboard."
      footer={<>New to AEOMirror? <button className="au-authlink" onClick={() => navigate("/register")}>Create an account</button></>}
    >
      <form className="au-form" onSubmit={submit}>
        {err && <AuAlert>{err}</AuAlert>}
        <AuField label="Email">
          <AuTextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     required autoFocus placeholder="you@company.com" autoComplete="email" />
        </AuField>
        <AuField label="Password">
          <AuPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                         required autoComplete="current-password" />
        </AuField>
        <div className="au-auth-row">
          <label className="au-check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember me
          </label>
          <button type="button" className="au-authlink" onClick={() => navigate("/forgot-password")}>Forgot password?</button>
        </div>
        <AuSubmitButton busy={busy}>Sign in</AuSubmitButton>
      </form>
    </AuAuthShell>
  );
}
