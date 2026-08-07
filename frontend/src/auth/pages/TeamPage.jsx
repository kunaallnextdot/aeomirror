import React, { useEffect, useState } from "react";
import { UserPlus, Trash2, Mail, X } from "lucide-react";
import { Field, TextInput, Alert, Avatar, RoleBadge } from "../ui.jsx";
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
    <div className="acct" style={{ maxWidth: 760 }}>
      {err && <div style={{ marginBottom: 14 }}><Alert>{err}</Alert></div>}

      {canInvite && (
        <div className="d-panel" style={{ marginBottom: 16 }}>
          <div className="d-panel-h"><UserPlus size={15} /> Invite a teammate</div>
          <form onSubmit={sendInvite} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <Field label="Email">
                <TextInput type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)}
                           required placeholder="teammate@company.com" />
              </Field>
            </div>
            <div>
              <div className="f-label" style={{ marginBottom: 6 }}>Role</div>
              <select className="f-select" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} style={{ padding: "10px 12px" }}>
                {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <button type="submit" className="btn btn-primary" disabled={inviteBusy}>
              {inviteBusy ? "Sending…" : "Send invite"}
            </button>
          </form>
          {inviteMsg && <div style={{ marginTop: 12 }}><Alert kind={inviteMsg.kind}>{inviteMsg.text}</Alert></div>}
        </div>
      )}

      <div className="d-panel" style={{ marginBottom: canInvite ? 16 : 0 }}>
        <div className="d-panel-h">Team members <span className="sub">{members ? `${members.length}` : ""}</span></div>
        {members === null ? <div className="d-dim">Loading…</div> : members.map((m) => {
          const isMe = m.user_id === user?.id;
          return (
            <div key={m.id} className="mem-row">
              <Avatar user={m} size={38} />
              <div className="mem-id">
                <div className="mem-name">{m.name} {isMe && <span className="pill-you">you</span>}</div>
                <div className="mem-email">{m.email}</div>
              </div>
              {canManage && !m.is_owner ? (
                <select className="f-select" value={m.role} onChange={(e) => changeRole(m, e.target.value)}>
                  {ASSIGNABLE.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              ) : (
                <RoleBadge role={m.role} />
              )}
              {canManage && !m.is_owner && (
                <button className="btn btn-ghost btn-sm" onClick={() => kick(m)} title="Remove member"><Trash2 size={13} /></button>
              )}
            </div>
          );
        })}
      </div>

      {canInvite && invites.length > 0 && (
        <div className="d-panel">
          <div className="d-panel-h"><Mail size={15} /> Pending invitations <span className="sub">{invites.length}</span></div>
          {invites.map((inv) => (
            <div key={inv.id} className="mem-row">
              <Mail size={18} style={{ color: "var(--txt-dim)" }} />
              <div className="mem-id">
                <div className="mem-name">{inv.email}</div>
                <div className="mem-email">invited as {inv.role} · expires {inv.expires_at ? new Date(inv.expires_at).toLocaleDateString() : "—"}</div>
              </div>
              <RoleBadge role={inv.role} />
              <button className="btn btn-ghost btn-sm" onClick={() => cancel(inv)} title="Cancel invitation"><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
