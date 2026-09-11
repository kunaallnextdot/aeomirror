import React, { useEffect, useState } from "react";
import {
  AuAuthShell, AuField, AuTextInput, AuPasswordInput, AuPasswordStrength, AuAlert, AuSubmitButton,
} from "../../dashboard/aurora.jsx";
import { passwordStrength } from "../ui.jsx";
import { useAuth } from "../AuthContext.jsx";
import { navigate } from "../router.jsx";
import { hasPendingScan } from "../pendingScan.js";

export default function Register() {
  const { register, isAuthenticated } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [organizationName, setOrg] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const strong = passwordStrength(password).ok;

  // A pending homepage scan sends the new user back to the scanner (which auto-runs
  // it); otherwise land in the dashboard. Email verification is NOT required first.
  useEffect(() => {
    if (isAuthenticated) navigate(hasPendingScan() ? "/" : "/app", { replace: true });
  }, [isAuthenticated]);

  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (!strong) { setErr("Please choose a stronger password (8+ chars, a letter and a number)."); return; }
    setBusy(true);
    try {
      await register({ name, email, password, organizationName });
    } catch (ex) {
      setErr(ex.message || "Could not create your account.");
      setBusy(false);
    }
  };

  return (
    <AuAuthShell
      title="Create your account"
      subtitle="Start tracking your AI visibility. You'll own a workspace you can invite your team to."
      footer={<>Already have an account? <button className="au-authlink" onClick={() => navigate("/login")}>Sign in</button></>}
    >
      <form className="au-form" onSubmit={submit}>
        {err && <AuAlert>{err}</AuAlert>}
        <AuField label="Your name">
          <AuTextInput value={name} onChange={(e) => setName(e.target.value)} required autoFocus
                     placeholder="Ada Lovelace" autoComplete="name" />
        </AuField>
        <AuField label="Work email">
          <AuTextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                     required placeholder="you@company.com" autoComplete="email" />
        </AuField>
        <AuField label="Organization name" hint="Optional — defaults to your name's team.">
          <AuTextInput value={organizationName} onChange={(e) => setOrg(e.target.value)}
                     placeholder="Acme Inc." />
        </AuField>
        <AuField label="Password">
          <AuPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                         required autoComplete="new-password" />
        </AuField>
        <AuPasswordStrength value={password} />
        <AuSubmitButton busy={busy}>Create account</AuSubmitButton>
        <div className="au-field-hint" style={{ textAlign: "center" }}>
          We'll email you a link to verify your address.
        </div>
      </form>
    </AuAuthShell>
  );
}
