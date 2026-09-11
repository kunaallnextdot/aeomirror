import React, { useState } from "react";
import {
  AuAuthShell, AuField, AuPasswordInput, AuPasswordStrength, AuAlert, AuSubmitButton,
} from "../../dashboard/aurora.jsx";
import { passwordStrength } from "../ui.jsx";
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
      <AuAuthShell title="Reset your password"
        footer={<button className="au-authlink" onClick={() => navigate("/forgot-password")}>Request a new link</button>}>
        <AuAlert>This reset link is missing its token. Please use the link from your email.</AuAlert>
      </AuAuthShell>
    );
  }

  return (
    <AuAuthShell
      title="Set a new password"
      subtitle="Choose a new password for your account."
      footer={<button className="au-authlink" onClick={() => navigate("/login")}>Back to sign in</button>}
    >
      {done ? (
        <>
          <AuAlert kind="ok">Your password has been reset. You can now sign in.</AuAlert>
          <button className="au-btn au-accent au-block" style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
            Go to sign in
          </button>
        </>
      ) : (
        <form className="au-form" onSubmit={submit}>
          {err && <AuAlert>{err}</AuAlert>}
          <AuField label="New password">
            <AuPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="new-password" />
          </AuField>
          <AuPasswordStrength value={password} />
          <AuField label="Confirm new password">
            <AuPasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)}
                           required autoComplete="new-password" />
          </AuField>
          <AuSubmitButton busy={busy}>Reset password</AuSubmitButton>
        </form>
      )}
    </AuAuthShell>
  );
}
