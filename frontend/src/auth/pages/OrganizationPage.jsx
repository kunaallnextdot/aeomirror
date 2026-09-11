import React, { useEffect, useState } from "react";
import { Shell, AuField, AuTextInput, AuAlert } from "../../dashboard/aurora.jsx";
import { getOrg, updateOrg } from "../api.js";
import { useAuth } from "../AuthContext.jsx";

export default function OrganizationPage() {
  const { hasPermission, refreshMe } = useAuth();
  const canEdit = hasPermission("org:update");
  const [org, setOrg] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    getOrg().then((o) => { setOrg(o); setName(o.name); }).catch((e) => setErr(e.message));
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      const updated = await updateOrg(name);
      setOrg(updated);
      await refreshMe();
      setMsg("Organization updated.");
    } catch (ex) {
      setMsg(null); setErr(ex.message || "Could not update the organization.");
    } finally { setBusy(false); }
  };

  if (err) return (
    <div className="aurora-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 720 }}><AuAlert>{err}</AuAlert></div>
    </Shell></div>
  );
  if (!org) return (
    <div className="aurora-screen"><Shell>
      <div className="au-dim" style={{ position: "relative", zIndex: 1 }}>Loading…</div>
    </Shell></div>
  );

  return (
    <div className="aurora-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 720 }}>
        <div className="au-panel">
          <div className="au-panel-h">Organization</div>
          <form className="au-form" onSubmit={save}>
            {msg && <AuAlert kind="ok">{msg}</AuAlert>}
            <AuField label="Name">
              <AuTextInput value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} required />
            </AuField>
            <AuField label="Workspace URL slug" hint="Used in links. Generated from the name.">
              <AuTextInput value={org.slug} disabled />
            </AuField>
            <AuField label="Created">
              <AuTextInput value={org.created_at ? new Date(org.created_at).toLocaleDateString() : "—"} disabled />
            </AuField>
            {canEdit ? (
              <button type="submit" className="au-btn au-accent" disabled={busy} style={{ alignSelf: "flex-start" }}>
                {busy ? "Saving…" : "Save changes"}
              </button>
            ) : (
              <div className="au-field-hint">Only the organization owner or an admin can change these settings.</div>
            )}
          </form>
        </div>
      </div>
    </Shell></div>
  );
}
