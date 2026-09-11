import React, { useEffect, useState } from "react";
import { BadgeCheck, AlertTriangle, Monitor, LogOut, Trash2 } from "lucide-react";
import { passwordStrength } from "../ui.jsx";
import {
  Shell, AuField, AuTextInput, AuPasswordInput, AuPasswordStrength, AuAlert, AuAvatar,
} from "../../dashboard/aurora.jsx";
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
    <div className="aurora-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 720 }}>
        {/* identity */}
        <div className="au-panel" style={{ marginBottom: 16 }}>
          <div className="au-panel-h">Profile</div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
            <AuAvatar user={{ ...user, name, avatar }} size={56} />
            <div>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{user?.name}</div>
              <div style={{ fontSize: 12.5, color: "var(--au-muted)", display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                {user?.email}
                {user?.email_verified
                  ? <span style={{ color: "var(--au-mint-d)", display: "inline-flex", alignItems: "center", gap: 4 }}><BadgeCheck size={13} /> verified</span>
                  : <span style={{ color: "var(--au-lemon-d)", display: "inline-flex", alignItems: "center", gap: 4 }}><AlertTriangle size={13} /> unverified</span>}
              </div>
            </div>
          </div>
          {!user?.email_verified && (
            <div style={{ marginBottom: 14 }}>
              <AuAlert kind="info">Your email isn't verified yet. <button className="au-authlink" onClick={resend}>Resend verification</button>{verifyMsg ? ` — ${verifyMsg}` : ""}</AuAlert>
            </div>
          )}
          <form className="au-form" onSubmit={saveProfile}>
            {profileMsg && <AuAlert kind={profileMsg.kind}>{profileMsg.text}</AuAlert>}
            <AuField label="Name">
              <AuTextInput value={name} onChange={(e) => setName(e.target.value)} required />
            </AuField>
            <AuField label="Avatar URL" hint="Paste an image URL, or leave blank to use your initial.">
              <AuTextInput value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="https://…" />
            </AuField>
            <div>
              <div className="au-field-l" style={{ marginBottom: 8 }}>Notifications</div>
              <label className="au-check" style={{ marginBottom: 8 }}>
                <input type="checkbox" checked={!!prefs.product_updates} onChange={() => togglePref("product_updates")} /> Product updates
              </label>
              <label className="au-check">
                <input type="checkbox" checked={!!prefs.scan_reports} onChange={() => togglePref("scan_reports")} /> Scan report emails
              </label>
            </div>
            <button type="submit" className="au-btn au-accent" disabled={savingProfile} style={{ alignSelf: "flex-start" }}>
              {savingProfile ? "Saving…" : "Save changes"}
            </button>
          </form>
        </div>

        {/* password */}
        <div className="au-panel" style={{ marginBottom: 16 }}>
          <div className="au-panel-h">Change password</div>
          <form className="au-form" onSubmit={changePassword}>
            {pwMsg && <AuAlert kind={pwMsg.kind}>{pwMsg.text}</AuAlert>}
            <AuField label="Current password">
              <AuPasswordInput value={curPw} onChange={(e) => setCurPw(e.target.value)} required autoComplete="current-password" />
            </AuField>
            <AuField label="New password">
              <AuPasswordInput value={newPw} onChange={(e) => setNewPw(e.target.value)} required autoComplete="new-password" />
            </AuField>
            <AuPasswordStrength value={newPw} />
            <button type="submit" className="au-btn au-accent" disabled={pwBusy} style={{ alignSelf: "flex-start" }}>
              {pwBusy ? "Updating…" : "Update password"}
            </button>
          </form>
        </div>

        {/* sessions */}
        <div className="au-panel">
          <div className="au-panel-h">Active sessions <span className="au-sub">devices signed in to your account</span></div>
          {sessions === null ? (
            <div className="au-dim">Loading…</div>
          ) : sessions.length === 0 ? (
            <div className="au-dim">No other active sessions.</div>
          ) : (
            <div>
              {sessions.map((s) => (
                <div key={s.id} className="au-mem-row">
                  <Monitor size={18} style={{ color: "var(--au-muted)" }} />
                  <div className="au-mem-id">
                    <div className="au-mem-name">{shortAgent(s.user_agent)} {s.current && <span className="au-pill-you">this device</span>}</div>
                    <div className="au-mem-email">last used {new Date(s.last_used_at || s.created_at).toLocaleString()}</div>
                  </div>
                  {!s.current && (
                    <button className="au-btn au-ghost au-sm" onClick={() => doRevoke(s.id)}><Trash2 size={13} /> Revoke</button>
                  )}
                </div>
              ))}
            </div>
          )}
          <button className="au-btn au-danger au-sm" style={{ marginTop: 14 }} onClick={logoutAll}>
            <LogOut size={13} /> Sign out everywhere
          </button>
        </div>
      </div>
    </Shell></div>
  );
}
