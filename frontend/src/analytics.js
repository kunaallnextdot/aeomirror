// Analytics bootstrap (Phase 10). Everything is env-gated and privacy-conscious —
// nothing loads unless the corresponding ID is configured at build time, so dev and
// self-hosted builds ship zero third-party scripts.
//
//   VITE_GA_ID              Google Analytics 4 measurement id (G-XXXXXXX)
//   VITE_CLARITY_ID         Microsoft Clarity project id
//   VITE_GSC_VERIFICATION   Google Search Console meta verification token

function loadScript(src, attrs = {}) {
  const s = document.createElement("script");
  s.async = true; s.src = src;
  Object.entries(attrs).forEach(([k, v]) => s.setAttribute(k, v));
  document.head.appendChild(s);
  return s;
}

function initGA4(id) {
  window.dataLayer = window.dataLayer || [];
  window.gtag = function gtag() { window.dataLayer.push(arguments); };
  loadScript(`https://www.googletagmanager.com/gtag/js?id=${id}`);
  window.gtag("js", new Date());
  window.gtag("config", id, { anonymize_ip: true });
}

function initClarity(id) {
  (function (c, l, a, r, i, t, y) {
    c[a] = c[a] || function () { (c[a].q = c[a].q || []).push(arguments); };
    t = l.createElement(r); t.async = 1; t.src = "https://www.clarity.ms/tag/" + i;
    y = l.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t, y);
  })(window, document, "clarity", "script", id);
}

function addVerification(token) {
  const m = document.createElement("meta");
  m.name = "google-site-verification"; m.content = token;
  document.head.appendChild(m);
}

export function initAnalytics() {
  const env = import.meta.env;
  if (env.VITE_GSC_VERIFICATION) addVerification(env.VITE_GSC_VERIFICATION);
  if (env.VITE_GA_ID) initGA4(env.VITE_GA_ID);
  if (env.VITE_CLARITY_ID) initClarity(env.VITE_CLARITY_ID);
}

// Basic product analytics — a thin wrapper so the app can record key events
// (signup, upgrade, scan) without knowing which providers are configured.
export function track(event, params = {}) {
  try {
    if (window.gtag) window.gtag("event", event, params);
    if (window.clarity) window.clarity("event", event);
  } catch { /* analytics must never break the app */ }
}
