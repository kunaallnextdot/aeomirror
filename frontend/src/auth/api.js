// Auth + organization API layer (Phase 5). Thin wrappers over apiJson; each sets
// the in-memory access token where appropriate. Errors are AuthError instances.
import { apiJson, setAccessToken } from "./client.js";

function adopt(body) {
  if (body && body.access_token) setAccessToken(body.access_token);
  return body;
}

// ------------------------------- auth -------------------------------
export async function register({ name, email, password, organizationName }) {
  return adopt(await apiJson("/auth/register", {
    method: "POST", auth: false,
    body: { name, email, password, organization_name: organizationName || null },
  }));
}

export async function login({ email, password, remember }) {
  return adopt(await apiJson("/auth/login", {
    method: "POST", auth: false, body: { email, password, remember: !!remember },
  }));
}

export async function logout() {
  try { await apiJson("/auth/logout", { method: "POST", auth: false }); }
  finally { setAccessToken(null); }
}

export async function logoutEverywhere() {
  try { await apiJson("/auth/logout-all", { method: "POST" }); }
  finally { setAccessToken(null); }
}

export function forgotPassword(email) {
  return apiJson("/auth/forgot-password", { method: "POST", auth: false, body: { email } });
}

export function resetPassword(token, password) {
  return apiJson("/auth/reset-password", { method: "POST", auth: false, body: { token, password } });
}

export function verifyEmail(token) {
  return apiJson("/auth/verify-email", { method: "POST", auth: false, body: { token } });
}

export function resendVerification(email) {
  return apiJson("/auth/resend-verification", { method: "POST", auth: false, body: { email } });
}

// ------------------------------- profile / me -------------------------------
export function getMe() {
  return apiJson("/me");
}

export function updateMe(patch) {
  return apiJson("/me", { method: "PATCH", body: patch });
}

export function listSessions() {
  return apiJson("/me/sessions");
}

export function revokeSession(id) {
  return apiJson(`/me/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ------------------------------- organization / team -------------------------------
export function getOrg() {
  return apiJson("/org");
}

export function updateOrg(name) {
  return apiJson("/org", { method: "PATCH", body: { name } });
}

export function listMembers() {
  return apiJson("/org/members");
}

export function updateMemberRole(membershipId, role) {
  return apiJson(`/org/members/${encodeURIComponent(membershipId)}`, { method: "PATCH", body: { role } });
}

export function removeMember(membershipId) {
  return apiJson(`/org/members/${encodeURIComponent(membershipId)}`, { method: "DELETE" });
}

export function listInvitations() {
  return apiJson("/org/invitations");
}

export function createInvitation(email, role) {
  return apiJson("/org/invitations", { method: "POST", body: { email, role } });
}

export function cancelInvitation(id) {
  return apiJson(`/org/invitations/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function acceptInvitation({ token, name, password }) {
  return adopt(await apiJson("/org/invitations/accept", {
    method: "POST", auth: false, body: { token, name, password },
  }));
}
