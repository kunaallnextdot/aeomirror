/* Shared, realistic /integrations/google/intelligence and /events payloads for the AI
   Traffic dashboard tests. Shapes mirror backend/app/services/integrations/ai_intelligence.py. */
const DEFINITIONS = {
  attribution: "Session-level: each conversion is credited to the source that started the session it happened in. GA4 provides aggregated data only, so there is no user-level journey across sessions.",
  conversions: "Event count of the events chosen as conversions (a session with two purchases counts twice).",
  conversions_per_100_sessions: "Conversions ÷ sessions from the same source and period × 100.",
  share_pct: "Sessions from the source ÷ all sessions in the period × 100.",
  ai_traffic: "Sessions whose referrer was a recognised AI assistant. Google AI Overviews clicks look like ordinary Google organic clicks in GA4 and are counted as organic search.",
  revenue: "GA4 totalRevenue in the property's currency, attributed to the session's source.",
  landing_key_events: "Landing-page conversions are GA4 key events (the page report has no per-event split).",
};

const row = (cls, label, sessions, share, conversions, per100, revenue = null) => ({
  class: cls, label, sessions, share_pct: share, engagement_rate_pct: 50.0,
  conversions, conversions_per_100_sessions: per100, revenue,
});

export function intelFixture(over = {}) {
  const measured = over.measured ?? true;
  const base = {
    synced: true,
    date_range: { start: "2026-10-06", end: "2026-11-02" },
    currency: "USD",
    overview: { total_sessions: 200, ai_sessions: 50, ai_share_pct: 25.0, organic_search_sessions: 100,
                total_conversions: measured ? 9 : null, daily: [] },
    conversions: {
      measured, status: measured ? "available" : "not_configured",
      definition: measured
        ? { mode: "ga4_key_events", events: ["purchase"], types: { purchase: "purchase" }, revenue_events: ["purchase"] }
        : { mode: "none", events: [], types: {}, revenue_events: [] },
      total: measured ? 9 : null, by_type: measured ? { purchase: 9 } : {},
      groups: {
        ai: row("ai", "All AI assistants", 50, 25.0, measured ? 4 : null, measured ? 8.0 : null, 300),
        organic_search: row("organic_search", "Organic search", 100, 50.0, measured ? 5 : null, measured ? 5.0 : null, 500),
      },
      funnel: null, revenue_status: "available",
    },
    landing_pages: { pages: [], source_split_complete: true, unsplit_sessions: 0 },
    comparison: [
      row("organic_search", "Organic search", 100, 50.0, measured ? 5 : null, measured ? 5.0 : null, 500),
      row("direct", "Direct", 50, 25.0, measured ? 0 : null, measured ? 0.0 : null, 0),
      row("chatgpt", "ChatGPT", 42, 21.0, measured ? 4 : null, measured ? 9.52 : null, 300),
      row("perplexity", "Perplexity", 8, 4.0, measured ? 0 : null, measured ? 0.0 : null, 0),
    ],
    freshness: { last_synced_at: "2026-11-02T08:00:00", events_synced_at: "2026-11-02T08:00:00",
                 landing_pages_synced_at: "2026-11-02T08:00:00", stale: false, covered_from: "2026-08-01",
                 partial_range: false, processing_lag_from: "2026-11-01", data_quality: {} },
    definitions: DEFINITIONS,
  };
  return { ...base, ...over, conversions: { ...base.conversions, ...(over.conversions || {}) } };
}

export function eventsFixture(over = {}) {
  return {
    synced: true, events_synced_at: "2026-11-02T08:00:00", key_events_known: true,
    date_range: { start: "2026-10-06", end: "2026-11-02" },
    events: [
      { event_name: "page_view", event_count: 900, seen_in_range: true, is_key_event: false, automatic: true, mapping: null, suggestion: null },
      { event_name: "purchase", event_count: 9, seen_in_range: true, is_key_event: true, automatic: false, mapping: null,
        suggestion: { role: "conversion", conversion_type: "purchase", counts_revenue: true } },
      { event_name: "generate_lead", event_count: 3, seen_in_range: true, is_key_event: false, automatic: false, mapping: null,
        suggestion: { role: "conversion", conversion_type: "lead", counts_revenue: false } },
    ],
    standard_events: [
      { event_name: "purchase", role: "conversion", conversion_type: "purchase", status: "collected" },
      { event_name: "booking_completed", role: "conversion", conversion_type: "booking", status: "not_seen" },
    ],
    conversion_definition: { mode: "ga4_key_events", events: ["purchase"] },
    note: "These are the events your website already sends to GA4. AEOMirror adds no tracking; an event that is 'not seen' must be added on the website (or in Google Tag Manager) before it can be measured.",
    ...over,
  };
}
