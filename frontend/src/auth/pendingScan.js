/* Pending-scan handoff: when a signed-out visitor submits a URL on the homepage we
   stash it here, send them through Register/Login, then auto-run it once they're
   authenticated — so they never have to re-type the URL. Uses sessionStorage so it
   survives the redirect but not a new tab/session. */
const KEY = "aeo_pending_scan";

export function setPendingScan(url, mode = "single") {
  try { sessionStorage.setItem(KEY, JSON.stringify({ url, mode })); } catch { /* ignore */ }
}

// Read + clear (single use), for the scanner to auto-run.
export function takePendingScan() {
  try {
    const v = sessionStorage.getItem(KEY);
    if (v) sessionStorage.removeItem(KEY);
    return v ? JSON.parse(v) : null;
  } catch { return null; }
}

// Read without clearing, for auth pages to decide where to redirect after success.
export function hasPendingScan() {
  try { return !!sessionStorage.getItem(KEY); } catch { return false; }
}

/* One-free-scan gate for signed-out visitors, enforced PER BROWSER (localStorage, so
   it survives reloads). Not per-IP: many real users share an IP behind NAT, so a
   per-IP cap would wrongly block first-time visitors. */
const FREE_USED_KEY = "aeo_free_scan_used";

export function freeScanUsed() {
  try { return localStorage.getItem(FREE_USED_KEY) === "1"; } catch { return false; }
}

export function markFreeScanUsed() {
  try { localStorage.setItem(FREE_USED_KEY, "1"); } catch { /* ignore */ }
}
