import React, { useEffect, useRef, useState } from "react";
import { SpAuthShell, SpAlert } from "../../shared/spectrum.jsx";
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
    <SpAuthShell title="Email verification">
      {state === "verifying" && (
        <div className="sp-alert sp-alert-info"><Loader2 size={15} className="sp-spin-icon" /> <span>Verifying your email…</span></div>
      )}
      {state === "missing" && <SpAlert>This verification link is missing its token. Use the link from your email.</SpAlert>}
      {state === "error" && <SpAlert>This verification link is invalid or has expired. You can request a new one from your profile.</SpAlert>}
      {state === "ok" && (
        <>
          <SpAlert kind="ok">Your email is verified. Thanks for confirming!</SpAlert>
          <button className="sp-btn sp-gradient sp-block" style={{ marginTop: 16 }} onClick={goNext}>
            {isAuthenticated ? "Go to dashboard" : "Continue to sign in"}
          </button>
        </>
      )}
    </SpAuthShell>
  );
}
