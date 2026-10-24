import React, { useState } from "react";
import {
  SpAuthShell, SpField, SpPasswordInput, SpPasswordStrength, SpAlert, SpSubmitButton,
} from "../../shared/spectrum.jsx";
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
      <SpAuthShell title="Reset your password"
        footer={<button className="sp-authlink" onClick={() => navigate("/forgot-password")}>Request a new link</button>}>
        <SpAlert>This reset link is missing its token. Please use the link from your email.</SpAlert>
      </SpAuthShell>
    );
  }

  return (
    <SpAuthShell
      title="Set a new password"
      subtitle="Choose a new password for your account."
      footer={<button className="sp-authlink" onClick={() => navigate("/login")}>Back to sign in</button>}
    >
      {done ? (
        <>
          <SpAlert kind="ok">Your password has been reset. You can now sign in.</SpAlert>
          <button className="sp-btn sp-gradient sp-block" style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
            Go to sign in
          </button>
        </>
      ) : (
        <form className="sp-form" onSubmit={submit}>
          {err && <SpAlert>{err}</SpAlert>}
          <SpField label="New password">
            <SpPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="new-password" />
          </SpField>
          <SpPasswordStrength value={password} />
          <SpField label="Confirm new password">
            <SpPasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)}
                           required autoComplete="new-password" />
          </SpField>
          <SpSubmitButton busy={busy}>Reset password</SpSubmitButton>
        </form>
      )}
    </SpAuthShell>
  );
}
