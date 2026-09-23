/* Phase 3 — AI Visibility rendering: the pure view renders grounded metrics, the
   answerability verdict, provider/gap/competitor/opportunity sections, free-tier locked
   previews, and a clean empty state. No network — components take data props directly. */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AIVisibilityView, AIVisibilityEmpty } from "./AIVisibility.jsx";

const DATA = {
  unlocked: true,
  visibility: {
    mention_rate: 42, missed_rate: 58, analyzed_count: 28, mentions: 12,
    citation_count: 3, competitor_mentions: 9, average_position: 2.0,
    per_provider: [
      { provider: "openai", samples: 14, mentions: 7, mention_rate: 50 },
      { provider: "anthropic", samples: 14, mentions: 5, mention_rate: 36 },
    ],
    excluded_extraction_failures: 0,
  },
  answerability: {
    verdict: "partially_visible", verdict_label: "Partially visible",
    evidence: "Your brand appeared in 9 of 24 analyzed prompt(s).",
    not_visible: [{ prompt_id: "p3", prompt: "best alternatives?", samples: 4, recommended_entities: ["Rival"] }],
  },
  content_gaps: [
    { prompt_id: "p3", prompt: "best alternatives?", why: "The site blocks GPTBot.",
      actions: ["Allow GPTBot"], has_signal: true, insufficient_evidence: false },
  ],
  competitor_intelligence: {
    brand: { name: "Acme", rank: 2, appearance_rate: 42, average_position: 2.0, rank_delta: -1 },
    competitors: [{ name: "Rival", rank: 1, appearance_rate: 58, average_position: 1.5, rank_delta: 1, tracked: true }],
    head_to_head: [{ competitor: "Rival", count: 3,
      statement: "Rival appeared in 3 tracked answers while your brand was absent.", prompts: [] }],
  },
  opportunities: {
    impact_label: "Opportunity Impact",
    items: [{ id: "answer_gap:p3", type: "answer_gap", title: "Not mentioned for a tracked query",
              description: "Absent from this answer.", priority: "Critical", impact: 100,
              recommended_action: "Allow GPTBot", affected_prompts: [], affected_urls: [] }],
  },
  trend: { direction: "improving", delta: 5, config_changed: false, runs: [{}, {}] },
};

describe("AIVisibilityView", () => {
  it("renders grounded metrics + verdict + all sections", () => {
    render(<AIVisibilityView data={DATA} />);
    expect(screen.getByText("Partially visible")).toBeTruthy();
    expect(screen.getByText(/9 of 24 analyzed/)).toBeTruthy();
    expect(screen.getAllByText(/42%/).length).toBeGreaterThan(0);  // mention rate
    expect(screen.getByText("openai")).toBeTruthy();           // provider breakdown
    expect(screen.getByText("anthropic")).toBeTruthy();
    expect(screen.getAllByText(/best alternatives\?/).length).toBeGreaterThan(0);  // gap prompt
    expect(screen.getByText("Rival")).toBeTruthy();            // competitor
    expect(screen.getByText(/while your brand was absent/)).toBeTruthy();  // head-to-head language
    expect(screen.getByText(/Not mentioned for a tracked query/)).toBeTruthy();  // opportunity
    // never claims a winner
    expect(screen.queryByText(/competitor.*won/i)).toBeNull();
  });

  it("shows free-tier locked previews and can open upgrade", () => {
    const openUpgrade = vi.fn();
    const free = {
      ...DATA, unlocked: false,
      visibility: { ...DATA.visibility, per_provider: DATA.visibility.per_provider.slice(0, 2), locked_provider_count: 2 },
      competitor_intelligence: { ...DATA.competitor_intelligence, head_to_head: [], head_to_head_locked: true, preview: true },
      opportunities: { ...DATA.opportunities, preview: true, locked_count: 5 },
    };
    render(<AIVisibilityView data={free} openUpgrade={openUpgrade} />);
    expect(screen.getByText(/2 more provider/)).toBeTruthy();
    expect(screen.getByText(/5 more opportunit/)).toBeTruthy();
  });
});

describe("AIVisibilityView opportunity grouping (redundancy cleanup)", () => {
  const GROUPED = {
    ...DATA,
    opportunities: {
      impact_label: "Opportunity Impact",
      items: [
        { id: "entity_gap:primary", type: "entity_opportunity", title: "Incomplete entity signals",
          description: "Missing 3 of 6 checked signal(s).", priority: "Critical", impact: 83.3,
          recommended_action: "Add Organization or LocalBusiness JSON-LD.",
          affected_prompts: [], affected_urls: ["https://acme.example/"],
          is_primary: true, root_cause_id: "schema" },
        { id: "score_loss:schema", type: "score_loss", title: "Improve Structured Data",
          description: "Structured Data scored 30/100.", priority: "Critical", impact: 70.0,
          recommended_action: null, affected_prompts: [], affected_urls: ["https://acme.example/"],
          is_primary: false, root_cause_id: "schema" },
        { id: "schema_gap:organization", type: "schema_opportunity", title: "Missing Organization structured data",
          description: "Your organization/entity information is not consistently represented.",
          priority: "High", impact: 70.0, recommended_action: "Add Organization JSON-LD.",
          affected_prompts: [], affected_urls: ["https://acme.example/"],
          is_primary: false, root_cause_id: "schema" },
        { id: "answer_gap:p9", type: "answer_gap", title: "Not mentioned in AI answers for a tracked query",
          description: "Your brand was not mentioned in any answer for this query.", priority: "Low",
          impact: 40.0, recommended_action: null, affected_prompts: [{ prompt_id: "p9", prompt: "distinct question?" }],
          affected_urls: [], is_primary: true, root_cause_id: null },
      ],
      groups: [{ root_cause_id: "schema", label: "Organization/entity structured data is incomplete",
                primary_id: "entity_gap:primary",
                opportunity_ids: ["entity_gap:primary", "score_loss:schema", "schema_gap:organization"],
                combined_impact: 83.3 }],
    },
  };

  it("renders exactly one primary card per root cause, not one per underlying item", () => {
    render(<AIVisibilityView data={GROUPED} />);
    expect(screen.getByText("Incomplete entity signals")).toBeTruthy();
    // the two non-primary group members are NOT rendered as their own top-level cards
    expect(screen.queryByText("Improve Structured Data")).toBeNull();
    expect(screen.queryByText("Missing Organization structured data")).toBeNull();
    // a genuinely distinct opportunity (no shared root cause) still renders on its own
    expect(screen.getByText("Not mentioned in AI answers for a tracked query")).toBeTruthy();
  });

  it("shows the root cause label and lets the user expand supporting evidence", () => {
    render(<AIVisibilityView data={GROUPED} />);
    expect(screen.getByText(/Organization\/entity structured data is incomplete/)).toBeTruthy();
    const toggle = screen.getByText(/Show 2 supporting evidence items/);
    expect(toggle).toBeTruthy();
    // evidence is not deleted — it's just collapsed until asked for
    expect(screen.queryByText(/Structured Data scored 30\/100/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText(/Improve Structured Data/)).toBeTruthy();
    expect(screen.getByText(/Missing Organization structured data/)).toBeTruthy();
  });

  it("ungrouped opportunities render with no root-cause label or toggle", () => {
    render(<AIVisibilityView data={GROUPED} />);
    const card = screen.getByText("Not mentioned in AI answers for a tracked query").closest(".au-av-opp");
    expect(card.textContent).not.toMatch(/Root cause:/);
    expect(card.textContent).not.toMatch(/supporting evidence/);
  });
});

describe("AIVisibilityEmpty", () => {
  it("renders a clean no-data state (no fabricated metrics)", () => {
    render(<AIVisibilityEmpty reason="no_run" />);
    expect(screen.getByText("No data yet")).toBeTruthy();
    expect(screen.getByText(/Run Answer Tracking/)).toBeTruthy();
    expect(screen.queryByText("%")).toBeNull();
  });
});
