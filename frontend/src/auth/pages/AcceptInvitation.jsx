import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  AuAuthShell, AuField, AuTextInput, AuPasswordInput, AuPasswordStrength,
  AuAlert, AuSubmitButton, AuRoleBadge,
} from "../../dashboard/aurora.jsx";
import { passwordStrength } from "../ui.jsx";
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
      <AuAuthShell title="Team invitation"
        footer={<button className="au-authlink" onClick={() => navigate("/login")}>Go to sign in</button>}>
        <AuAlert>{loadErr}</AuAlert>
      </AuAuthShell>
    );
  }
  if (!info) {
    return (
      <AuAuthShell title="Team invitation">
        <div className="au-alert au-alert-info"><Loader2 size={15} className="au-spin" /> <span>Loading your invitation…</span></div>
      </AuAuthShell>
    );
  }

  return (
    <AuAuthShell
      title={`Join ${info.organization_name}`}
      subtitle={<span>You've been invited as <AuRoleBadge role={info.role} /> · <b>{info.email}</b></span>}
      footer={<button className="au-authlink" onClick={() => navigate("/login")}>Use a different account</button>}
    >
      <form className="au-form" onSubmit={submit}>
        {err && <AuAlert>{err}</AuAlert>}
        {requiresSignup ? (
          <>
            <AuField label="Your name">
              <AuTextInput value={name} onChange={(e) => setName(e.target.value)} required autoFocus
                         placeholder="Ada Lovelace" autoComplete="name" />
            </AuField>
            <AuField label="Create a password">
              <AuPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                             required autoComplete="new-password" />
            </AuField>
            <AuPasswordStrength value={password} />
          </>
        ) : (
          <AuField label="Your password" hint="Enter your existing AEOMirror password to accept.">
            <AuPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="current-password" />
          </AuField>
        )}
        <AuSubmitButton busy={busy}>Accept invitation</AuSubmitButton>
      </form>
    </AuAuthShell>
  );
}
