import React, { useState } from "react";
import { AuAuthShell, AuField, AuTextInput, AuAlert, AuSubmitButton } from "../../dashboard/aurora.jsx";
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
    <AuAuthShell
      title="Reset your password"
      subtitle="Enter your email and we'll send you a link to set a new password."
      footer={<button className="au-authlink" onClick={() => navigate("/login")}>Back to sign in</button>}
    >
      {sent ? (
        <AuAlert kind="ok">If an account exists for that email, a reset link is on its way. Check your inbox.</AuAlert>
      ) : (
        <form className="au-form" onSubmit={submit}>
          {err && <AuAlert>{err}</AuAlert>}
          <AuField label="Email">
            <AuTextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                       required autoFocus placeholder="you@company.com" autoComplete="email" />
          </AuField>
          <AuSubmitButton busy={busy}>Send reset link</AuSubmitButton>
        </form>
      )}
    </AuAuthShell>
  );
}
