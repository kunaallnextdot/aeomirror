// Route guards for the authenticated app + admin platform.
//
// CRITICAL: while the initial session restore is still resolving (`!ready`) we render
// a loader and DO NOT redirect — redirecting during an unresolved auth check bounces
// a signed-in user to /login on every hard refresh. Only once `ready` is true and the
// user is definitively unauthenticated do we redirect, preserving the intended
// destination in both location state and a `?next=` query param so login can return
// the user there.
import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "../auth/AuthContext.jsx";

export function FullScreenLoader() {
  return (
    <div style={{ minHeight: "60vh", display: "flex", alignItems: "center", justifyContent: "center",
                  color: "var(--txt-mid)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Loader2 size={18} className="spin-slow" /> Loading…
      </div>
    </div>
  );
}

export function RequireAuth({ children }) {
  const { ready, isAuthenticated } = useAuth();
  const location = useLocation();
  if (!ready) return <FullScreenLoader />;          // unresolved — never redirect
  if (!isAuthenticated) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`}
                     state={{ from: next }} replace />;
  }
  return children || <Outlet />;
}

export function RequireAdmin({ children }) {
  const { ready, isAuthenticated, user } = useAuth();
  const location = useLocation();
  if (!ready) return <FullScreenLoader />;
  if (!isAuthenticated) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`}
                     state={{ from: next }} replace />;
  }
  if (!user?.is_platform_admin) return <Navigate to="/app/dashboard" replace />;
  return children || <Outlet />;
}
