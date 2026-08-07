import React, { useEffect, useState } from "react";
import { AuthShell, Field, TextInput, PasswordInput, Alert, SubmitButton } from "../ui.jsx";
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

  // Redirect once auth state has actually committed (race-free vs. the guard). A
  // pending homepage scan returns to the scanner (auto-runs it); else the dashboard.
  useEffect(() => {
    if (isAuthenticated) navigate(hasPendingScan() ? "/" : "/app", { replace: true });
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
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to your AEOMirror dashboard."
      footer={<>New to AEOMirror? <button className="auth-link" onClick={() => navigate("/register")}>Create an account</button></>}
    >
      <form className="auth-form" onSubmit={submit}>
        {err && <Alert>{err}</Alert>}
        <Field label="Email">
          <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     required autoFocus placeholder="you@company.com" autoComplete="email" />
        </Field>
        <Field label="Password">
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                         required autoComplete="current-password" />
        </Field>
        <div className="auth-row">
          <label className="f-check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember me
          </label>
          <button type="button" className="auth-link" onClick={() => navigate("/forgot-password")}>Forgot password?</button>
        </div>
        <SubmitButton busy={busy}>Sign in</SubmitButton>
      </form>
    </AuthShell>
  );
}
