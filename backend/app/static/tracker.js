/*! AEOMirror first-party analytics tracker — GA4-independent.
 * Install: <script defer src="https://YOUR-API-HOST/t/tracker.js" data-site-id="YOUR_SITE_ID"></script>
 * Public API: window.aeomirror.track(eventType, { value_cents, currency, external_id, event_name })
 *
 * Privacy: collects only page URLs, referrer, UTM params, and whatever a conversion
 * call explicitly passes (never form contents, names, or emails). A long-lived,
 * first-party visitor id and a session id are stored in this browser only (localStorage
 * / sessionStorage) — never a cross-site identifier, never a cookie. Respects
 * navigator.doNotTrack and an explicit window.aeomirrorConsent = false opt-out, checked
 * BEFORE anything is generated or sent.
 */
(function () {
  "use strict";

  if (window.aeomirror) return; // already installed on this page

  var SESSION_TIMEOUT_MS = 30 * 60 * 1000; // keep in sync with analytics_session_timeout_minutes
  var VISITOR_KEY = "aeomirror_visitor_id";
  var SESSION_KEY = "aeomirror_session";

  function consentGiven() {
    if (window.aeomirrorConsent === false) return false;
    if (navigator.doNotTrack === "1" || window.doNotTrack === "1") return false;
    return true;
  }
  if (!consentGiven()) return;

  var scriptEl = document.currentScript || (function () {
    var scripts = document.getElementsByTagName("script");
    return scripts[scripts.length - 1];
  })();
  var siteId = scriptEl && scriptEl.getAttribute("data-site-id");
  if (!siteId) return; // misconfigured install — fail silently, never break the host page

  var apiOrigin = (function () {
    try { return new URL(scriptEl.src).origin; } catch (e) { return ""; }
  })();
  var COLLECT_URL = apiOrigin + "/t/collect";

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function safeGet(storage, key) {
    try { return storage.getItem(key); } catch (e) { return null; }
  }
  function safeSet(storage, key, val) {
    try { storage.setItem(key, val); } catch (e) { /* storage unavailable — degrade silently */ }
  }

  function getVisitorId() {
    var id = safeGet(window.localStorage, VISITOR_KEY);
    if (!id) { id = uuid(); safeSet(window.localStorage, VISITOR_KEY, id); }
    return id;
  }

  function getSession() {
    var now = Date.now();
    var raw = safeGet(window.sessionStorage, SESSION_KEY);
    var parsed = null;
    try { parsed = raw ? JSON.parse(raw) : null; } catch (e) { parsed = null; }
    if (parsed && now - parsed.lastActivity < SESSION_TIMEOUT_MS) {
      parsed.lastActivity = now;
      safeSet(window.sessionStorage, SESSION_KEY, JSON.stringify(parsed));
      return parsed;
    }
    var fresh = { id: uuid(), lastActivity: now };
    safeSet(window.sessionStorage, SESSION_KEY, JSON.stringify(fresh));
    return fresh;
  }

  function utmParam(name) {
    try { return new URLSearchParams(window.location.search).get(name) || undefined; }
    catch (e) { return undefined; }
  }

  var visitorId = getVisitorId();
  var queue = [];
  var flushTimer = null;

  function send(immediate) {
    if (!queue.length) return;
    var batch = { site_id: siteId, events: queue.splice(0, queue.length) };
    var payload = JSON.stringify(batch);
    var sent = false;
    if (navigator.sendBeacon) {
      try {
        var blob = new Blob([payload], { type: "application/json" });
        sent = navigator.sendBeacon(COLLECT_URL, blob);
      } catch (e) { sent = false; }
    }
    if (!sent) {
      try {
        fetch(COLLECT_URL, {
          method: "POST", body: payload, keepalive: true,
          headers: { "Content-Type": "text/plain" }, // avoids a CORS preflight
        }).catch(function () {});
      } catch (e) { /* never let a tracking failure surface to the host page */ }
    }
  }

  function scheduleFlush(immediate) {
    if (immediate) { send(); return; }
    if (flushTimer) return;
    flushTimer = setTimeout(function () { flushTimer = null; send(); }, 300);
  }

  function enqueue(event) {
    queue.push(event);
    scheduleFlush(queue.length >= 10);
  }

  var lastTrackedUrl = null;

  function trackPageview() {
    var href = window.location.href;
    if (href === lastTrackedUrl) return; // SPA route change firing twice (push+pop) — dedupe
    lastTrackedUrl = href;
    var session = getSession();
    enqueue({
      event_id: uuid(), type: "pageview", session_id: session.id, visitor_id: visitorId,
      url: href, referrer: document.referrer || undefined,
      utm_source: utmParam("utm_source"), utm_medium: utmParam("utm_medium"),
      utm_campaign: utmParam("utm_campaign"),
    });
  }

  function track(eventType, opts) {
    opts = opts || {};
    var session = getSession();
    enqueue({
      event_id: uuid(), type: "conversion", session_id: session.id, visitor_id: visitorId,
      url: window.location.href, event_type: eventType, event_name: opts.event_name,
      value_cents: opts.value_cents, currency: opts.currency,
      external_id: opts.external_id || uuid(),
    });
    scheduleFlush(true); // conversions matter — flush immediately, don't wait for the debounce
  }

  // Initial pageview.
  trackPageview();

  // SPA navigation: wrap pushState/replaceState (covers router.push) and listen for
  // popstate (back/forward) and hashchange — each just re-checks the URL, so a
  // navigation that doesn't actually change location.href is a no-op (no double count).
  ["pushState", "replaceState"].forEach(function (method) {
    var original = history[method];
    history[method] = function () {
      var result = original.apply(this, arguments);
      trackPageview();
      return result;
    };
  });
  window.addEventListener("popstate", trackPageview);
  window.addEventListener("hashchange", trackPageview);

  // Flush any queued events before the page is torn down.
  window.addEventListener("pagehide", function () { send(); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") send();
  });

  window.aeomirror = { track: track, visitorId: visitorId };
})();
