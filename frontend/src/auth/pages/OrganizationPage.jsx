import React, { useEffect, useState } from "react";
import { Shell, SpField, SpTextInput, SpAlert } from "../../shared/spectrum.jsx";
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
    <div className="spectrum-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 720 }}><SpAlert>{err}</SpAlert></div>
    </Shell></div>
  );
  if (!org) return (
    <div className="spectrum-screen"><Shell>
      <div className="sp-dim" style={{ position: "relative", zIndex: 1 }}>Loading…</div>
    </Shell></div>
  );

  return (
    <div className="spectrum-screen"><Shell>
      <div style={{ position: "relative", zIndex: 1, maxWidth: 720 }}>
        <div className="sp-panel">
          <div className="sp-panel-h">Organization</div>
          <form className="sp-form" onSubmit={save}>
            {msg && <SpAlert kind="ok">{msg}</SpAlert>}
            <SpField label="Name">
              <SpTextInput value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} required />
            </SpField>
            <SpField label="Workspace URL slug" hint="Used in links. Generated from the name.">
              <SpTextInput value={org.slug} disabled />
            </SpField>
            <SpField label="Created">
              <SpTextInput value={org.created_at ? new Date(org.created_at).toLocaleDateString() : "—"} disabled />
            </SpField>
            {canEdit ? (
              <button type="submit" className="sp-btn sp-gradient" disabled={busy} style={{ alignSelf: "flex-start" }}>
                {busy ? "Saving…" : "Save changes"}
              </button>
            ) : (
              <div className="sp-field-hint">Only the organization owner or an admin can change these settings.</div>
            )}
          </form>
        </div>
      </div>
    </Shell></div>
  );
}
