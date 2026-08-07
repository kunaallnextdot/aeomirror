import React, { useState } from "react";
import {
  AuthShell, Field, PasswordInput, PasswordStrength, passwordStrength, Alert, SubmitButton,
} from "../ui.jsx";
import { resetPassword } from "../api.js";
import { navigate, queryParam } from "../router.jsx";

export default function ResetPassword() {
  const token = queryParam("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (!passwordStrength(password).ok) { setErr("Choose a stronger password (8+ chars, a letter and a number)."); return; }
    if (password !== confirm) { setErr("The passwords don't match."); return; }
    setBusy(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (ex) {
      setErr(ex.message || "Could not reset your password.");
    } finally { setBusy(false); }
  };

  if (!token) {
    return (
      <AuthShell title="Reset your password"
        footer={<button className="auth-link" onClick={() => navigate("/forgot-password")}>Request a new link</button>}>
        <Alert>This reset link is missing its token. Please use the link from your email.</Alert>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Set a new password"
      subtitle="Choose a new password for your account."
      footer={<button className="auth-link" onClick={() => navigate("/login")}>Back to sign in</button>}
    >
      {done ? (
        <>
          <Alert kind="ok">Your password has been reset. You can now sign in.</Alert>
          <button className="btn btn-primary btn-block" style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
            Go to sign in
          </button>
        </>
      ) : (
        <form className="auth-form" onSubmit={submit}>
          {err && <Alert>{err}</Alert>}
          <Field label="New password">
            <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="new-password" />
          </Field>
          <PasswordStrength value={password} />
          <Field label="Confirm new password">
            <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)}
                           required autoComplete="new-password" />
          </Field>
          <SubmitButton busy={busy}>Reset password</SubmitButton>
        </form>
      )}
    </AuthShell>
  );
}
