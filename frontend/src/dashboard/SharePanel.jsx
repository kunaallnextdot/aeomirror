/* Public share-link control for a report (owner-side, authenticated).
   The URL is RETRIEVABLE (no show-once): on open we list the scan's active share and
   render its URL + copy + expiry + view count + revoke; if none exists, an explicit
   "Create public link". A 402 (active-share cap) opens the upgrade modal. */
import React, { useCallback, useEffect, useState } from "react";
import { Share2, Clock, AlertTriangle, Check } from "lucide-react";
import { createShare, listShares, revokeShare, ScanError } from "../api.js";
import { useUpgrade } from "./UpgradeModal.jsx";

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function fmtDate(iso) {
  try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); }
  catch { return iso; }
}

export function SharePanel({ scanId }) {
  const { openUpgrade } = useUpgrade();
  const [open, setOpen] = useState(false);
  const [shares, setShares] = useState(null);   // null = loading, [] = none
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(() => {
    if (!scanId) return;
    setShares(null);
    listShares(scanId).then((r) => setShares(r.shares || [])).catch(() => setShares([]));
  }, [scanId]);

  useEffect(() => { if (open) refresh(); }, [open, refresh]);

  const share = shares && shares[0];
  const url = share ? `${window.location.origin}${share.path}` : null;
  const expiresSoon = share ? (new Date(share.expires_at).getTime() - Date.now()) < WEEK_MS : false;

  const create = async () => {
    setBusy(true); setErr(null);
    try { setShares([await createShare(scanId)]); }
    catch (e) {
      if (e instanceof ScanError && e.code === 402) openUpgrade("share", { message: e.message });
      else setErr(e instanceof ScanError ? e.message : "Could not create the link.");
    } finally { setBusy(false); }
  };

  const revoke = async () => {
    if (!share) return;
    setBusy(true); setErr(null);
    try { await revokeShare(share.id); setShares([]); setCopied(false); }
    catch (e) { setErr(e instanceof ScanError ? e.message : "Could not revoke the link."); }
    finally { setBusy(false); }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1800); }
    catch { /* clipboard blocked — the input is selectable as a fallback */ }
  };

  return (
    <div className="rep-sharewrap">
      <button className={`rep-dl rep-share${open ? " on" : ""}`} onClick={() => setOpen((o) => !o)}
              aria-expanded={open}>
        <Share2 size={15} /> Share
      </button>
      {open && (
        <div className="rep-share-pop">
          {shares === null ? (
            <div className="d-dim" style={{ fontSize: 12.5 }}>Loading…</div>
          ) : share ? (
            <>
              <div className="rep-share-h">Public link</div>
              <div className="rep-share-url">
                <input readOnly value={url} onFocus={(e) => e.target.select()} aria-label="Public report link" />
                <button className="btn btn-primary btn-sm" onClick={copy}>
                  {copied ? <><Check size={12} /> Copied</> : "Copy"}
                </button>
              </div>
              <div className="rep-share-meta d-dim">
                Expires {fmtDate(share.expires_at)} · {share.view_count} view{share.view_count === 1 ? "" : "s"}
              </div>
              {expiresSoon && (
                <div className="rep-share-warn">
                  <Clock size={12} /> This link expires {fmtDate(share.expires_at)}. Re-create it to keep it working.
                </div>
              )}
              <button className="rep-share-revoke" disabled={busy} onClick={revoke}>
                {busy ? "Revoking…" : "Revoke link"}
              </button>
              <div className="rep-share-note d-dim">
                Anyone with this link can view the report — no login needed. It isn't indexed by search engines.
              </div>
            </>
          ) : (
            <>
              <div className="rep-share-h">Share this report</div>
              <div className="d-dim" style={{ fontSize: 12.5, marginBottom: 10 }}>
                Create a public, read-only link. Anyone with it can view the report without signing in.
              </div>
              <button className="btn btn-primary btn-block" disabled={busy} onClick={create}>
                {busy ? "Creating…" : "Create public link"}
              </button>
            </>
          )}
          {err && <div className="rep-share-err"><AlertTriangle size={12} /> {err}</div>}
        </div>
      )}
    </div>
  );
}
