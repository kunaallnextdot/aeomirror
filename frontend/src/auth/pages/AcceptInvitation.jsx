import React, { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  SpAuthShell, SpField, SpTextInput, SpPasswordInput, SpPasswordStrength,
  SpAlert, SpSubmitButton, SpRoleBadge,
} from "../../shared/spectrum.jsx";
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
      <SpAuthShell title="Team invitation"
        footer={<button className="sp-authlink" onClick={() => navigate("/login")}>Go to sign in</button>}>
        <SpAlert>{loadErr}</SpAlert>
      </SpAuthShell>
    );
  }
  if (!info) {
    return (
      <SpAuthShell title="Team invitation">
        <div className="sp-alert sp-alert-info"><Loader2 size={15} className="sp-spin-icon" /> <span>Loading your invitation…</span></div>
      </SpAuthShell>
    );
  }

  return (
    <SpAuthShell
      title={`Join ${info.organization_name}`}
      subtitle={<span>You've been invited as <SpRoleBadge role={info.role} /> · <b>{info.email}</b></span>}
      footer={<button className="sp-authlink" onClick={() => navigate("/login")}>Use a different account</button>}
    >
      <form className="sp-form" onSubmit={submit}>
        {err && <SpAlert>{err}</SpAlert>}
        {requiresSignup ? (
          <>
            <SpField label="Your name">
              <SpTextInput value={name} onChange={(e) => setName(e.target.value)} required autoFocus
                         placeholder="Ada Lovelace" autoComplete="name" />
            </SpField>
            <SpField label="Create a password">
              <SpPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                             required autoComplete="new-password" />
            </SpField>
            <SpPasswordStrength value={password} />
          </>
        ) : (
          <SpField label="Your password" hint="Enter your existing AEOMirror password to accept.">
            <SpPasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                           required autoFocus autoComplete="current-password" />
          </SpField>
        )}
        <SpSubmitButton busy={busy}>Accept invitation</SpSubmitButton>
      </form>
    </SpAuthShell>
  );
}
