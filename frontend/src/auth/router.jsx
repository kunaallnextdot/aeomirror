// Navigation bridge.
//
// The app now uses react-router-dom for real URLs, deep links, and back/forward.
// This module keeps the small legacy API (`navigate`, `useLocation`, `queryParam`)
// that the marketing + auth pages already call, but backs it with react-router so
// every existing call site keeps working without a rewrite. `<RouterBridge/>` is
// mounted once inside <BrowserRouter> to capture react-router's imperative navigate.
import { useEffect } from "react";
import {
  useNavigate,
  useLocation as useRouterLocation,
} from "react-router-dom";

let _navigate = null;

// Legacy imperative navigate. Delegates to react-router once the bridge is mounted;
// before mount (should not happen in practice) it falls back to a hard location change.
export function navigate(to, { replace = false } = {}) {
  if (_navigate) _navigate(to, { replace });
  else if (typeof window !== "undefined") window.location.assign(to);
}

// Mounted once inside the router so `navigate()` above can reach react-router.
export function RouterBridge() {
  const nav = useNavigate();
  useEffect(() => {
    _navigate = nav;
    return () => { if (_navigate === nav) _navigate = null; };
  }, [nav]);
  return null;
}

// Legacy location shape { path, search } used by older components.
export function useLocation() {
  const loc = useRouterLocation();
  return { path: loc.pathname, search: loc.search };
}

export function queryParam(name) {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}
