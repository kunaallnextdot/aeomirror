// Authenticated fetch client (Phase 5).
//
// The access token lives only in memory (never localStorage) so it isn't exposed
// to XSS-persisted theft; the refresh token is an httpOnly cookie the browser
// sends automatically. authFetch attaches the Bearer token, and on a 401 it
// transparently attempts ONE token refresh (via the cookie) and retries. When the
// refresh fails, the session is cleared and subscribers are notified so the app
// can redirect to Login.

const API = import.meta.env.VITE_API_URL;

let accessToken = null;
let onCleared = null; // called when the session becomes invalid

export function setAccessToken(token) { accessToken = token || null; }
export function getAccessToken() { return accessToken; }
export function onSessionCleared(fn) { onCleared = fn; }
export function getApiBase() { return API; }

// A single in-flight refresh shared by concurrent 401s (no refresh stampede).
let refreshing = null;
export function refreshSession() {
  if (!refreshing) {
    refreshing = fetch(`${API}/auth/refresh`, { method: "POST", credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error("refresh_failed");
        const body = await r.json();
        accessToken = body.access_token;
        return body;
      })
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

function buildInit(opts) {
  const headers = new Headers(opts.headers || {});
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  // Never force JSON on a FormData body — the browser sets the multipart boundary.
  if (opts.body && !(opts.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return { ...opts, headers, credentials: "include" };
}

// Low-level: returns the raw Response (callers decide how to read it).
export async function authFetch(path, opts = {}, { retryOn401 = true } = {}) {
  let res = await fetch(`${API}${path}`, buildInit(opts));
  if (res.status === 401 && retryOn401) {
    try {
      await refreshSession();
    } catch {
      accessToken = null;
      if (onCleared) onCleared();
      return res; // surface the original 401
    }
    res = await fetch(`${API}${path}`, buildInit(opts));
  }
  return res;
}

export class AuthError extends Error {
  constructor(message, code) {
    super(message);
    this.name = "AuthError";
    this.code = code;
  }
}

function messageFor(status, detail) {
  const safe = typeof detail === "string" && detail.trim() ? detail.trim() : null;
  if (status === 401) return safe || "Please sign in to continue.";
  if (status === 403) return safe || "You don't have permission to do that.";
  if (status === 409) return safe || "That conflicts with something that already exists.";
  if (status === 422) return safe || "Please check the form and try again.";
  if (status === 429) return safe || "Too many attempts. Please wait a moment and try again.";
  if (status >= 500) return "Something went wrong on our end. Please try again.";
  return safe || `Request failed (status ${status}).`;
}

async function readDetail(res) {
  try {
    const body = await res.json();
    return body && typeof body.detail === "string" ? body.detail : null;
  } catch { return null; }
}

// JSON helper used by the auth/org API layer. Throws AuthError on failure.
// `auth: false` skips the automatic refresh-retry (used for the auth actions
// themselves: login/register/refresh/etc.).
export async function apiJson(path, { method = "GET", body, auth = true } = {}) {
  let res;
  const opts = { method, body: body !== undefined ? JSON.stringify(body) : undefined };
  try {
    res = auth ? await authFetch(path, opts) : await fetch(`${API}${path}`, buildInit(opts));
  } catch {
    throw new AuthError("Can't reach the server. Check your connection and try again.", "network");
  }
  if (!res.ok) throw new AuthError(messageFor(res.status, await readDetail(res)), res.status);
  return res.status === 204 ? null : await res.json();
}
