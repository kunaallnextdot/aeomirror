// Auth state provider (Phase 5).
//
// On mount it attempts a SILENT refresh using the httpOnly cookie so a returning
// user stays signed in across reloads (the access token itself is only in memory).
// Exposes the current user/org/role, the auth actions, and a client-side mirror of
// the backend permission matrix for UI gating (the server still enforces it).
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import { onSessionCleared, refreshSession, setAccessToken } from "./client.js";
import * as api from "./api.js";

const AuthCtx = createContext(null);

// Must mirror app/api/deps.py PERMISSIONS (server is authoritative).
const PERMISSIONS = {
  "report:view": ["viewer", "member", "admin", "owner"],
  "scan:run": ["member", "admin", "owner"],
  "scan:delete": ["admin", "owner"],
  "user:invite": ["admin", "owner"],
  "member:manage": ["owner"],
  "org:update": ["owner", "admin"],
};

export function AuthProvider({ children }) {
  const [ready, setReady] = useState(false);   // finished the initial restore
  const [user, setUser] = useState(null);
  const [org, setOrg] = useState(null);
  const [role, setRole] = useState(null);

  const applyMe = useCallback((me) => {
    setUser(me?.user || null);
    setOrg(me?.organization || null);
    setRole(me?.role || me?.user?.role || null);
  }, []);

  const clear = useCallback(() => {
    setAccessToken(null);
    setUser(null); setOrg(null); setRole(null);
  }, []);

  // Silent restore on first load. SKIPPED on the public shared-report route (/r/*):
  // a public page must fire NO auth call — a signed-out visitor should not trigger a
  // refresh cycle, and a signed-in one should not attach their session to that page.
  useEffect(() => {
    if (typeof window !== "undefined" && window.location.pathname.startsWith("/r/")) {
      setReady(true);
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        await refreshSession();               // throws if no valid cookie
        const me = await api.getMe();
        if (!cancelled) applyMe(me);
      } catch {
        if (!cancelled) clear();
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => { cancelled = true; };
  }, [applyMe, clear]);

  // When authFetch gives up on a 401, drop local state.
  useEffect(() => { onSessionCleared(() => clear()); }, [clear]);

  const login = useCallback(async (creds) => {
    const body = await api.login(creds);
    applyMe({ user: body.user, organization: body.organization, role: body.user.role });
    return body;
  }, [applyMe]);

  const register = useCallback(async (data) => {
    const body = await api.register(data);
    applyMe({ user: body.user, organization: body.organization, role: body.user.role });
    return body;
  }, [applyMe]);

  const acceptInvitation = useCallback(async (data) => {
    const body = await api.acceptInvitation(data);
    applyMe({ user: body.user, organization: body.organization, role: body.user.role });
    return body;
  }, [applyMe]);

  const logout = useCallback(async () => {
    try { await api.logout(); } finally { clear(); }
  }, [clear]);

  const refreshMe = useCallback(async () => {
    const me = await api.getMe();
    applyMe(me);
    return me;
  }, [applyMe]);

  const hasPermission = useCallback((action) => {
    if (!role) return false;
    if (role === "owner") return true;
    return (PERMISSIONS[action] || []).includes(role);
  }, [role]);

  const value = useMemo(() => ({
    ready,
    isAuthenticated: !!user,
    user, org, role,
    login, register, acceptInvitation, logout, refreshMe,
    setUserData: setUser,
    hasPermission,
  }), [ready, user, org, role, login, register, acceptInvitation, logout, refreshMe, hasPermission]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
