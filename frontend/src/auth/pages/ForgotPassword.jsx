import React, { useState } from "react";
import { SpAuthShell, SpField, SpTextInput, SpAlert, SpSubmitButton } from "../../shared/spectrum.jsx";
import { forgotPassword } from "../api.js";
import { navigate } from "../router.jsx";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (ex) {
      setErr(ex.message || "Could not send the reset link.");
    } finally { setBusy(false); }
  };

  return (
    <SpAuthShell
      title="Reset your password"
      subtitle="Enter your email and we'll send you a link to set a new password."
      footer={<button className="sp-authlink" onClick={() => navigate("/login")}>Back to sign in</button>}
    >
      {sent ? (
        <SpAlert kind="ok">If an account exists for that email, a reset link is on its way. Check your inbox.</SpAlert>
      ) : (
        <form className="sp-form" onSubmit={submit}>
          {err && <SpAlert>{err}</SpAlert>}
          <SpField label="Email">
            <SpTextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                       required autoFocus placeholder="you@company.com" autoComplete="email" />
          </SpField>
          <SpSubmitButton busy={busy}>Send reset link</SpSubmitButton>
        </form>
      )}
    </SpAuthShell>
  );
}
