/* AI Traffic client-side insights: every claim must be supported by the complete
   data it ranks or compares, never by one filtered/paginated table page or an
   implied baseline. buildInsights is pure; api.js is mocked only so the module loads. */
import { describe, it, expect, vi } from "vitest";

vi.mock("../api.js", () => ({ ScanError: class extends Error {} }));
vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => ({ hasPermission: () => true }) }));
vi.mock("./UpgradeModal.jsx", () => ({ useUpgrade: () => ({ openUpgrade: vi.fn() }) }));

import { buildInsights } from "./AiTraffic.jsx";

const row = (channel, source, sessions, extra = {}) =>
  ({ channel, source, medium: "x", sessions, ai_source_label: "other_identifiable", ...extra });
const texts = (out) => out.map((i) => i.text).join("\n");

describe("buildInsights", () => {
  it("ranks channels by their summed rows, not by a single source/medium row", () => {
    const channels = { total: 3, channels: [
      row("Referral", "a.example", 30), row("Organic Search", "google", 25), row("Organic Search", "bing", 20)] };
    expect(texts(buildInsights({ channels }))).toMatch(/Organic Search generated 45 sessions .* the most of any channel/);
  });

  it("makes no 'most of any channel' claim from a filtered or partial table page", () => {
    const channels = { total: 40, channels: [row("Referral", "a.example", 30)] };
    expect(texts(buildInsights({ channels }))).not.toMatch(/most of any channel/);
    const full = { total: 1, channels: [row("Referral", "a.example", 30)] };
    expect(texts(buildInsights({ channels: full, filtered: true }))).not.toMatch(/most of any channel/);
  });

  it("makes no ranking claim on a tie", () => {
    const channels = { total: 2, channels: [row("Referral", "a", 10), row("Direct", "(direct)", 10)] };
    expect(texts(buildInsights({ channels }))).not.toMatch(/most of any channel/);
  });

  it("reports AI assistant sessions from the full /traffic totals", () => {
    const traffic = { by_source_detail: { chatgpt: { sessions: 12 }, perplexity: { sessions: 0 } } };
    expect(texts(buildInsights({ traffic }))).toMatch(/ChatGPT .* generated 12 sessions/);
  });

  it("compares page engagement only against the property-wide rate, and only when it exists", () => {
    const landingPages = { pages: [{ landing_page: "/a", sessions: 40, engagement_rate: 0.2 }] };
    expect(texts(buildInsights({ landingPages }))).toBe("");              // no baseline → no comparison
    const overview = { current: { engagement_rate: 0.55 } };
    expect(texts(buildInsights({ landingPages, overview }))).toMatch(/\/a received 40 sessions .* below the 55(\.0)?% rate for the whole property/);
    expect(texts(buildInsights({ landingPages, overview }))).not.toMatch(/other pages/);
  });
});
