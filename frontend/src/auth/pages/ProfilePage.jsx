import React, { useEffect, useState } from "react";
import { BadgeCheck, AlertTriangle, Monitor, LogOut, Trash2 } from "lucide-react";
import {
  Field, TextInput, PasswordInput, PasswordStrength, passwordStrength, Alert, Avatar,
} from "../ui.jsx";
import {
  updateMe, listSessions, revokeSession, logoutEverywhere, resendVerification,
} from "../api.js";
import { useAuth } from "../AuthContext.jsx";

function shortAgent(ua) {
  if (!ua) return "Unknown device";
  const b = /Firefox/.test(ua) ? "Firefox" : /Edg/.test(ua) ? "Edge"
    : /Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : "Browser";
  const os = /Mac/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows"
    : /Linux/.test(ua) ? "Linux" : /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : "";
  return os ? `${b} · ${os}` : b;
}

export default function ProfilePage() {
  const { user, refreshMe, logout } = useAuth();
  const [name, setName] = useState(user?.name || "");
  const [avatar, setAvatar] = useState(user?.avatar || "");
  const [prefs, setPrefs] = useState(user?.notification_prefs || {});
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState(null);

  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwMsg, setPwMsg] = useState(null);

  const [sessions, setSessions] = useState(null);
  const [verifyMsg, setVerifyMsg] = useState(null);

  useEffect(() => { listSessions().then(setSessions).catch(() => setSessions([])); }, []);

  const saveProfile = async (e) => {
    e.preventDefault();
    setProfileMsg(null); setSavingProfile(true);
    try {
      await updateMe({ name, avatar: avatar || null, notification_prefs: prefs });
      await refreshMe();
      setProfileMsg({ kind: "ok", text: "Profile updated." });
    } catch (ex) {
      setProfileMsg({ kind: "error", text: ex.message || "Could not update profile." });
    } finally { setSavingProfile(false); }
  };

  const changePassword = async (e) => {
    e.preventDefault();
    setPwMsg(null);
    if (!passwordStrength(newPw).ok) { setPwMsg({ kind: "error", text: "Choose a stronger password." }); return; }
    setPwBusy(true);
    try {
      await updateMe({ current_password: curPw, new_password: newPw });
      setCurPw(""); setNewPw("");
      setPwMsg({ kind: "ok", text: "Password changed." });
    } catch (ex) {
      setPwMsg({ kind: "error", text: ex.message || "Could not change password." });
    } finally { setPwBusy(false); }
  };

  const togglePref = (key) => setPrefs((p) => ({ ...p, [key]: !p[key] }));

  const resend = async () => {
    setVerifyMsg(null);
    try { await resendVerification(user.email); setVerifyMsg("Verification email sent."); }
    catch { setVerifyMsg("Could not send the email right now."); }
  };

  const doRevoke = async (id) => {
    await revokeSession(id).catch(() => {});
    setSessions((s) => (s || []).filter((x) => x.id !== id));
  };

  const logoutAll = async () => {
    if (!window.confirm("Sign out of all other sessions?")) return;
    try { await logoutEverywhere(); await logout(); } catch { /* ignore */ }
  };

  return (
    <div className="acct">
      {/* identity */}
      <div className="d-panel" style={{ marginBottom: 16 }}>
        <div className="d-panel-h">Profile</div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
          <Avatar user={{ ...user, name, avatar }} size={56} />
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{user?.name}</div>
            <div style={{ fontSize: 12.5, color: "var(--txt-mid)", display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
              {user?.email}
              {user?.email_verified
                ? <span style={{ color: "var(--good)", display: "inline-flex", alignItems: "center", gap: 4 }}><BadgeCheck size={13} /> verified</span>
                : <span style={{ color: "var(--warn)", display: "inline-flex", alignItems: "center", gap: 4 }}><AlertTriangle size={13} /> unverified</span>}
            </div>
          </div>
        </div>
        {!user?.email_verified && (
          <div style={{ marginBottom: 14 }}>
            <Alert kind="info">Your email isn't verified yet. <button className="auth-link" onClick={resend}>Resend verification</button>{verifyMsg ? ` — ${verifyMsg}` : ""}</Alert>
          </div>
        )}
        <form className="auth-form" onSubmit={saveProfile}>
          {profileMsg && <Alert kind={profileMsg.kind}>{profileMsg.text}</Alert>}
          <Field label="Name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <Field label="Avatar URL" hint="Paste an image URL, or leave blank to use your initial.">
            <TextInput value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="https://…" />
          </Field>
          <div>
            <div className="f-label" style={{ marginBottom: 8 }}>Notifications</div>
            <label className="f-check" style={{ marginBottom: 8 }}>
              <input type="checkbox" checked={!!prefs.product_updates} onChange={() => togglePref("product_updates")} /> Product updates
            </label>
            <label className="f-check">
              <input type="checkbox" checked={!!prefs.scan_reports} onChange={() => togglePref("scan_reports")} /> Scan report emails
            </label>
          </div>
          <button type="submit" className="btn btn-primary" disabled={savingProfile} style={{ alignSelf: "flex-start" }}>
            {savingProfile ? "Saving…" : "Save changes"}
          </button>
        </form>
      </div>

      {/* password */}
      <div className="d-panel" style={{ marginBottom: 16 }}>
        <div className="d-panel-h">Change password</div>
        <form className="auth-form" onSubmit={changePassword}>
          {pwMsg && <Alert kind={pwMsg.kind}>{pwMsg.text}</Alert>}
          <Field label="Current password">
            <PasswordInput value={curPw} onChange={(e) => setCurPw(e.target.value)} required autoComplete="current-password" />
          </Field>
          <Field label="New password">
            <PasswordInput value={newPw} onChange={(e) => setNewPw(e.target.value)} required autoComplete="new-password" />
          </Field>
          <PasswordStrength value={newPw} />
          <button type="submit" className="btn btn-primary" disabled={pwBusy} style={{ alignSelf: "flex-start" }}>
            {pwBusy ? "Updating…" : "Update password"}
          </button>
        </form>
      </div>

      {/* sessions */}
      <div className="d-panel">
        <div className="d-panel-h">Active sessions <span className="sub">devices signed in to your account</span></div>
        {sessions === null ? (
          <div className="d-dim">Loading…</div>
        ) : sessions.length === 0 ? (
          <div className="d-dim">No other active sessions.</div>
        ) : (
          <div>
            {sessions.map((s) => (
              <div key={s.id} className="mem-row">
                <Monitor size={18} style={{ color: "var(--txt-dim)" }} />
                <div className="mem-id">
                  <div className="mem-name">{shortAgent(s.user_agent)} {s.current && <span className="pill-you">this device</span>}</div>
                  <div className="mem-email">last used {new Date(s.last_used_at || s.created_at).toLocaleString()}</div>
                </div>
                {!s.current && (
                  <button className="btn btn-ghost btn-sm" onClick={() => doRevoke(s.id)}><Trash2 size={13} /> Revoke</button>
                )}
              </div>
            ))}
          </div>
        )}
        <button className="btn btn-danger btn-sm" style={{ marginTop: 14 }} onClick={logoutAll}>
          <LogOut size={13} /> Sign out everywhere
        </button>
      </div>
    </div>
  );
}
