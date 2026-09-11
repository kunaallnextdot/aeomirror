/* Route-level error boundary for the authenticated tree.
 *
 * It distinguishes TWO failure kinds:
 *   1. Lazy-chunk load failures — almost always a stale tab requesting a hashed chunk
 *      that a new deploy invalidated. Recoverable: try exactly ONE automatic reload
 *      (guarded by a sessionStorage flag), and if that reload still fails, stop and show
 *      a manual "new version — reload" message. A chunk error NEVER shows a generic crash.
 *   2. Application runtime errors — NOT reloaded (a reload just reproduces them). These
 *      render the provided `fallback` (an in-layout error state so the sidebar stays
 *      usable) or a default full-screen error with a link back to the dashboard.
 *
 * Loop safety: the sessionStorage flag survives the reload. It is cleared only when the
 * authenticated chunk mounts successfully (see AppRoot -> clearChunkReloadFlag). So a
 * genuinely broken chunk fails, auto-reloads once, fails again with the flag still set,
 * and shows the manual message — it can never reload in a loop.
 */
import React from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, RefreshCw } from "lucide-react";

export const CHUNK_RELOAD_FLAG = "aeomirror:chunk_reloaded";

// Match the chunk-loading error signature across bundlers/browsers — NOT every error.
const CHUNK_RE =
  /Loading chunk|Loading CSS chunk|dynamically imported module|Importing a module script failed|ChunkLoadError/i;

export function isChunkLoadError(error) {
  if (!error) return false;
  if (error.name === "ChunkLoadError") return true;
  return CHUNK_RE.test(String(error.message || error));
}

// Best-effort: pull the attempted chunk URL out of the error message for logging.
export function chunkPathFromError(error) {
  const m = String((error && error.message) || error || "").match(/https?:\/\/[^\s'")]+/);
  return m ? m[0] : null;
}

// Cleared on a successful mount of the authenticated chunk — the thing that prevents an
// auto-reload loop (a still-broken chunk never mounts, so the flag stays set).
export function clearChunkReloadFlag() {
  try { sessionStorage.removeItem(CHUNK_RELOAD_FLAG); } catch { /* sessionStorage unavailable */ }
}

export class RouteErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, isChunk: false, reloading: false };
  }

  static getDerivedStateFromError(error) {
    return { error, isChunk: isChunkLoadError(error) };
  }

  componentDidCatch(error, info) {
    if (isChunkLoadError(error)) {
      const path = chunkPathFromError(error);
      // eslint-disable-next-line no-console
      console.error("[chunk-load-failed]", path || "(unknown chunk)", error && error.message);
      let alreadyReloaded = false;
      try { alreadyReloaded = sessionStorage.getItem(CHUNK_RELOAD_FLAG) === "1"; } catch { /* ignore */ }
      if (!alreadyReloaded) {
        try { sessionStorage.setItem(CHUNK_RELOAD_FLAG, "1"); } catch { /* ignore */ }
        this.setState({ reloading: true });
        window.location.reload();           // exactly one automatic reload
      }
      // else: already tried once and it still failed -> keep the manual message below.
    } else {
      // eslint-disable-next-line no-console
      console.error("[route-error]", error, info && info.componentStack);
    }
  }

  render() {
    const { error, isChunk, reloading } = this.state;
    if (!error) return this.props.children;
    if (isChunk) {
      if (reloading) return <Centered title="Updating…" body="Loading the latest version." />;
      return <NewVersionNotice />;          // reload already happened and still failed
    }
    return this.props.fallback || <DefaultRuntimeError />;
  }
}

/* NOTE: Centered / NewVersionNotice / DefaultRuntimeError are the OUTER boundary's defaults
   (App.jsx wraps <AppRoot> with no fallback). They render on the still-dark `.root` ground
   BEFORE the Aurora `/app` shell mounts (e.g. a chunk-load failure), so they stay dark until
   the app-wide theme flip in #14. Only InLayoutErrorState below is guaranteed to render
   inside the light shell (it is the fallback passed from AppLayout), so only it is Aurora. */
function Centered({ title, body, children }) {
  return (
    <div style={{ minHeight: "60vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div className="d-panel" style={{ textAlign: "center", maxWidth: 420, padding: "28px 24px" }}>
        <AlertTriangle size={22} style={{ color: "var(--warn)" }} />
        <div style={{ fontSize: 15, fontWeight: 600, margin: "10px 0 4px" }}>{title}</div>
        <div className="d-dim" style={{ fontSize: 13, marginBottom: children ? 16 : 0 }}>{body}</div>
        {children}
      </div>
    </div>
  );
}

function NewVersionNotice() {
  return (
    <Centered title="A new version is available" body="Reload to continue.">
      <button className="d-btn primary" onClick={() => window.location.reload()}>
        <RefreshCw size={15} /> Reload
      </button>
    </Centered>
  );
}

function DefaultRuntimeError() {
  return (
    <Centered title="Something went wrong" body="This page hit an unexpected error.">
      <Link className="d-btn" to="/app/dashboard">Back to dashboard</Link>
    </Centered>
  );
}

/* In-layout runtime fallback: renders inside the content area so the sidebar stays usable
   and the user is never stranded on a blank page. */
export function InLayoutErrorState() {
  return (
    <div className="au-panel" style={{ textAlign: "center", padding: "36px 20px" }}>
      <AlertTriangle size={20} style={{ color: "var(--au-lemon-d)" }} />
      <div style={{ fontSize: 15, fontWeight: 600, margin: "8px 0 4px", color: "var(--au-ink)" }}>Something went wrong</div>
      <div className="au-dim" style={{ fontSize: 13, marginBottom: 14 }}>
        This view hit an unexpected error. Your other pages still work.
      </div>
      <Link className="au-btn au-accent" to="/app/dashboard">Back to dashboard</Link>
    </div>
  );
}
