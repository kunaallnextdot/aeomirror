import React, { useEffect, useState } from "react";
import { UserPlus, Trash2, Mail, X } from "lucide-react";
import { Shell, SpField, SpTextInput, SpAlert, SpAvatar, SpRoleBadge } from "../../shared/spectrum.jsx";
import {
  listMembers, updateMemberRole, removeMember,
  listInvitations, createInvitation, cancelInvitation,
} from "../api.js";
import { useAuth } from "../AuthContext.jsx";

const ASSIGNABLE = ["admin", "member", "viewer"];

export default function TeamPage() {
  const { user, hasPermission } = useAuth();
  const canInvite = hasPermission("user:invite");
  const canManage = hasPermission("member:manage");

  const [members, setMembers] = useState(null);
  const [invites, setInvites] = useState([]);
  const [err, setErr] = useState(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteMsg, setInviteMsg] = useState(null);

  const loadMembers = () => listMembers().then(setMembers).catch((e) => setErr(e.message));
  const loadInvites = () => { if (canInvite) listInvitations().then(setInvites).catch(() => {}); };

  useEffect(() => { loadMembers(); loadInvites(); /* eslint-disable-next-line */ }, []);

  const changeRole = async (m, role) => {
    try { await updateMemberRole(m.id, role); await loadMembers(); }
    catch (ex) { setErr(ex.message); }
  };

  const kick = async (m) => {
    if (!window.confirm(`Remove ${m.name} from the team?`)) return;
    try { await removeMember(m.id); setMembers((list) => list.filter((x) => x.id !== m.id)); }
    catch (ex) { setErr(ex.message); }
  };

  const sendInvite = async (e) => {
    e.preventDefault();
    setInviteMsg(null); setInviteBusy(true);
    try {
      await createInvitation(inviteEmail, inviteRole);
      setInviteEmail("");
      setInviteMsg({ kind: "ok", text: `Invitation sent to ${inviteEmail || "them"}.` });
      loadInvites();
    } catch (ex) {
      setInviteMsg({ kind: "error", text: ex.message || "Could not send the invitation." });
    } finally { setInviteBusy(false); }
  };

  const cancel = async (inv) => {
    try { await cancelInvitation(inv.id); setInvites((list) => list.filter((x) => x.id !== inv.id)); }
    catch (ex) { setErr(ex.message); }
  };

  return (
    <div className="spectrum-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 760 }}>
        {err && <div style={{ marginBottom: 14 }}><SpAlert>{err}</SpAlert></div>}

        {canInvite && (
          <div className="sp-panel" style={{ marginBottom: 16 }}>
            <div className="sp-panel-h"><UserPlus size={15} /> Invite a teammate</div>
            <form onSubmit={sendInvite} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <SpField label="Email">
                  <SpTextInput type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)}
                             required placeholder="teammate@company.com" />
                </SpField>
              </div>
              <div>
                <div className="sp-field-l" style={{ marginBottom: 6 }}>Role</div>
                <select className="sp-select" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} style={{ padding: "10px 12px" }}>
                  {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
              <button type="submit" className="sp-btn sp-gradient" disabled={inviteBusy}>
                {inviteBusy ? "Sending…" : "Send invite"}
              </button>
            </form>
            {inviteMsg && <div style={{ marginTop: 12 }}><SpAlert kind={inviteMsg.kind}>{inviteMsg.text}</SpAlert></div>}
          </div>
        )}

        <div className="sp-panel" style={{ marginBottom: canInvite ? 16 : 0 }}>
          <div className="sp-panel-h">Team members <span className="sp-sub">{members ? `${members.length}` : ""}</span></div>
          {members === null ? <div className="sp-dim">Loading…</div> : members.map((m) => {
            const isMe = m.user_id === user?.id;
            return (
              <div key={m.id} className="sp-mem-row">
                <SpAvatar user={m} size={38} />
                <div className="sp-mem-id">
                  <div className="sp-mem-name">{m.name} {isMe && <span className="sp-pill-you">you</span>}</div>
                  <div className="sp-mem-email">{m.email}</div>
                </div>
                {canManage && !m.is_owner ? (
                  <select className="sp-select" value={m.role} onChange={(e) => changeRole(m, e.target.value)}>
                    {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                ) : (
                  <SpRoleBadge role={m.role} />
                )}
                {canManage && !m.is_owner && (
                  <button className="sp-btn sp-ghost sp-sm" onClick={() => kick(m)} title="Remove member" aria-label={`Remove ${m.name || m.email || "member"}`}><Trash2 size={13} /></button>
                )}
              </div>
            );
          })}
        </div>

        {canInvite && invites.length > 0 && (
          <div className="sp-panel">
            <div className="sp-panel-h"><Mail size={15} /> Pending invitations <span className="sp-sub">{invites.length}</span></div>
            {invites.map((inv) => (
              <div key={inv.id} className="sp-mem-row">
                <Mail size={18} style={{ color: "var(--sp-muted)" }} />
                <div className="sp-mem-id">
                  <div className="sp-mem-name">{inv.email}</div>
                  <div className="sp-mem-email">invited as {inv.role} · expires {inv.expires_at ? new Date(inv.expires_at).toLocaleDateString() : "—"}</div>
                </div>
                <SpRoleBadge role={inv.role} />
                <button className="sp-btn sp-ghost sp-sm" onClick={() => cancel(inv)} title="Cancel invitation" aria-label={`Cancel invitation for ${inv.email}`}><X size={13} /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Shell></div>
  );
}
