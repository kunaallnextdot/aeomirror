import React, { useEffect, useState } from "react";
import { Field, TextInput, Alert } from "../ui.jsx";
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

  if (err) return <div className="acct"><Alert>{err}</Alert></div>;
  if (!org) return <div className="acct d-dim">Loading…</div>;

  return (
    <div className="acct">
      <div className="d-panel">
        <div className="d-panel-h">Organization</div>
        <form className="auth-form" onSubmit={save}>
          {msg && <Alert kind="ok">{msg}</Alert>}
          <Field label="Name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} required />
          </Field>
          <Field label="Workspace URL slug" hint="Used in links. Generated from the name.">
            <TextInput value={org.slug} disabled />
          </Field>
          <Field label="Created">
            <TextInput value={org.created_at ? new Date(org.created_at).toLocaleDateString() : "—"} disabled />
          </Field>
          {canEdit ? (
            <button type="submit" className="btn btn-primary" disabled={busy} style={{ alignSelf: "flex-start" }}>
              {busy ? "Saving…" : "Save changes"}
            </button>
          ) : (
            <div className="f-hint">Only the organization owner or an admin can change these settings.</div>
          )}
        </form>
      </div>
    </div>
  );
}
