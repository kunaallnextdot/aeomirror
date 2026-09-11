import React, { useEffect, useRef, useState } from "react";
import { AuAuthShell, AuAlert } from "../../dashboard/aurora.jsx";
import { Loader2 } from "lucide-react";
import { verifyEmail } from "../api.js";
import { useAuth } from "../AuthContext.jsx";
import { navigate, queryParam } from "../router.jsx";

export default function VerifyEmail() {
  const token = queryParam("token");
  const { isAuthenticated, refreshMe } = useAuth();
  const [state, setState] = useState(token ? "verifying" : "missing"); // verifying|ok|error|missing
  const ran = useRef(false);

  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    (async () => {
      try {
        await verifyEmail(token);
        if (isAuthenticated) { try { await refreshMe(); } catch { /* ignore */ } }
        setState("ok");
      } catch {
        setState("error");
      }
    })();
  }, [token, isAuthenticated, refreshMe]);

  const goNext = () => navigate(isAuthenticated ? "/app" : "/login");

  return (
    <AuAuthShell title="Email verification">
      {state === "verifying" && (
        <div className="au-alert au-alert-info"><Loader2 size={15} className="au-spin" /> <span>Verifying your email…</span></div>
      )}
      {state === "missing" && <AuAlert>This verification link is missing its token. Use the link from your email.</AuAlert>}
      {state === "error" && <AuAlert>This verification link is invalid or has expired. You can request a new one from your profile.</AuAlert>}
      {state === "ok" && (
        <>
          <AuAlert kind="ok">Your email is verified. Thanks for confirming!</AuAlert>
          <button className="au-btn au-accent au-block" style={{ marginTop: 16 }} onClick={goNext}>
            {isAuthenticated ? "Go to dashboard" : "Continue to sign in"}
          </button>
        </>
      )}
    </AuAuthShell>
  );
}
