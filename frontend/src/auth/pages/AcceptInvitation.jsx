import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  AuthShell, Field, TextInput, PasswordInput, PasswordStrength, passwordStrength,
  Alert, SubmitButton, RoleBadge,
} from "../ui.jsx";
import { apiJson } from "../client.js";
import { useAuth } from "../AuthContext.jsx";
import { navigate, queryParam } from "../router.jsx";

export default function AcceptInvitation() {
  const token = queryParam("token");
  const { acceptInvitation, isAuthenticated } = useAuth();
  const [info, setInfo] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!token) { setLoadErr("This invitation link is missing its token."); return; }
    apiJson(`/org/invitations/info?token=${encodeURIComponent(token)}`, { auth: false })
      .then(setInfo)
      .catch((ex) => setLoadErr(ex.message || "This invitation is invalid or has expired."));
  }, [token]);

  useEffect(() => { if (isAuthenticated) navigate("/app", { replace: true }); }, [isAuthenticated]);

  const requiresSignup = info?.requires_signup;

  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (requiresSignup && !passwordStrength(password).ok) {
      setErr("Choose a stronger password (8+ chars, a letter and a number).");
      return;
    }
    setBusy(true);
    try {
      await acceptInvitation({ token, name: requiresSignup ? name : undefined, password });
    } catch (ex) {
      setErr(ex.message || "Could not accept the invitation.");
      setBusy(false);
    }
  };

  if (loadErr) {
    return (
      <AuthShell title="Team invitation"
        footer={<button className="auth-link" onClick={() => navigate("/login")}>Go to sign in</button>}>
        <Alert>{loadErr}</Alert>
      </AuthShell>
    );
  }
  if (!info) {
    return (
      <AuthShell title="Team invitation">
        <div className="alert alert-info"><Loader2 size={15} className="spin-slow" /> <span>Loading your invitation…</span></div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={`Join ${info.organization_name}`}
      subtitle={<span>You've been invited as <RoleBadge role={info.role} /> · <b>{info.email}</b></span>}
      footer={<button className="auth-link" onClick={() => navigate("/login")}>Use a different account</button>}
    >
      <form className="auth-form" onSubmit={submit}>
        {err && <Alert>{err}</Alert>}
        {requiresSignup ? (
          <>
            <Field label="Your name">
              <TextInput value={name} onChange={(e) => setName(e.target.value)} required autoFocus
                         placeholder="Ada Lovelace" autoComplete="name" />
            </Field>
            <Field label="Create a password">
              <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                             required autoComplete="new-password" />
            </Field>
            <PasswordStrength value={password} />
          </>
        ) : (
          <Field label="Your password" hint="Enter your existing AEOMirror password to accept.">
            <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="current-password" />
          </Field>
        )}
        <SubmitButton busy={busy}>Accept invitation</SubmitButton>
      </form>
    </AuthShell>
  );
}
