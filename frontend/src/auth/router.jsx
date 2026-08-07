// Tiny path-based router (Phase 5) — no react-router dependency. Enough for the
// handful of top-level routes (marketing, auth pages, the protected app).
import { useEffect, useState } from "react";

export function navigate(to, { replace = false } = {}) {
  const current = window.location.pathname + window.location.search;
  if (to === current) return;
  if (replace) window.history.replaceState({}, "", to);
  else window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function useLocation() {
  const [loc, setLoc] = useState(() => ({
    path: window.location.pathname, search: window.location.search,
  }));
  useEffect(() => {
    const onPop = () => setLoc({
      path: window.location.pathname, search: window.location.search,
    });
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return loc;
}

export function queryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}
