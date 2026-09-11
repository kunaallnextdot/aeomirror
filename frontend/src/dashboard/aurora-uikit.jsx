/* DEV-ONLY Aurora UI kit — renders every Phase-3 primitive in every state for visual review.
   Reachable at /ui-kit ONLY in development (the route is gated on import.meta.env.DEV in
   App.jsx and lazy-loaded, so it is dead-code-eliminated from production builds).
   REMOVAL (final cleanup phase): delete this file + frontend/src/dashboard/aurora.jsx +
   aurora.css, and remove the `UiKit` lazy import and the `import.meta.env.DEV && <Route
   path="/ui-kit" .../>` line from src/App.jsx. Nothing else references them. */
import React, { useEffect, useState } from "react";
import {
  Shell, TopNav, PageHead, Bento, Cell, Metric, Tag, Button, Ring, ProgressBar,
  Skeleton, StepList, EngineCard, TypingDots, AURORA_ENGINES,
  Podium, Radar, PromptRowList,
} from "./aurora.jsx";

/* static fixtures shaped EXACTLY like the app's real data (no reshaping) */
const LB_NORMAL = [
  { rank: 1, name: "Profound", appearance_rate: 88, appearances: 8, is_you: false },
  { rank: 2, name: "Acme", appearance_rate: 62, appearances: 5, is_you: true },
  { rank: 3, name: "Clearscope", appearance_rate: 54, appearances: 5, is_you: false },
  { rank: 4, name: "Writesonic", appearance_rate: 33, appearances: 3, is_you: false },
  { rank: 5, name: "Surfer SEO", appearance_rate: 21, appearances: 2, is_you: false },
];
const LB_TIES = [
  { rank: 1, name: "Profound", appearance_rate: 60, appearances: 6, is_you: false },
  { rank: 1, name: "Acme", appearance_rate: 60, appearances: 6, is_you: true },
  { rank: 3, name: "Clearscope", appearance_rate: 40, appearances: 4, is_you: false },
];
const LB_ONE = [{ rank: 1, name: "Profound", appearance_rate: 75, appearances: 6, is_you: false }];
const LB_APPENDED = [
  { rank: 1, name: "Profound", appearance_rate: 90, appearances: 9, is_you: false },
  { rank: 2, name: "Clearscope", appearance_rate: 80, appearances: 8, is_you: false },
  { rank: 3, name: "Writesonic", appearance_rate: 70, appearances: 7, is_you: false },
  { rank: 4, name: "Surfer SEO", appearance_rate: 55, appearances: 6, is_you: false },
  { rank: 5, name: "MarketMuse", appearance_rate: 44, appearances: 5, is_you: false },
  { rank: 11, name: "Acme", appearance_rate: 12, appearances: 1, is_you: true },
];
const RADAR_AXES = [
  { category: "Crawlability", score: 82 }, { category: "Structured Data", score: 64 },
  { category: "Content Clarity", score: 73 }, { category: "Citations", score: 38 },
  { category: "Freshness", score: 56 }, { category: "Authority", score: 47 },
];
const RADAR_TWO = [{ category: "Crawlability", score: 80 }, { category: "Citations", score: 40 }];
const PROMPTS = [
  { prompt_id: "p1", text: "best AI visibility tools for SaaS marketing teams", mention_rate: 66, is_gap: false,
    engines: [{ engine: "openai", named: true }, { engine: "anthropic", named: false },
              { engine: "perplexity", named: true }, { engine: "gemini", named: false }] },
  { prompt_id: "p2", text: "how to track whether ChatGPT recommends my business and cites the right pages every time", mention_rate: 0, is_gap: true,
    engines: [{ engine: "openai", named: false }, { engine: "anthropic", named: false },
              { engine: "perplexity", named: false }, { engine: "gemini", named: false }] },
  { prompt_id: "p3", text: "answer engine optimization checklist", mention_rate: null, is_gap: false,
    engines: [{ engine: "openai", named: true }, { engine: "anthropic", named: true },
              { engine: "perplexity", named: false }, { engine: "gemini", named: true }] },
];

function Group({ title, children }) {
  return (<>
    <h2 className="au-kit-sec">{title}</h2>
    <div className="au-kit-row">{children}</div>
  </>);
}

export default function UiKit() {
  // Drive the animated primitives (progress + ring) so both zero and filled states are seen.
  const [pct, setPct] = useState(0);
  useEffect(() => { const t = setTimeout(() => setPct(72), 120); return () => clearTimeout(t); }, []);

  const navItems = [
    { label: "Overview", active: true }, { label: "Answers" },
    { label: "Monitoring" }, { label: "Report" },
  ];

  return (
    <div className="au-kit">
      <Shell>
        <TopNav
          items={navItems}
          credits={{ used: 128, total: 500 }}
          cta={{ label: "Run a scan" }}
          avatar="KS"
        />
        <div className="au-kit-body">
          <PageHead
            eyebrow="AEOMIRROR / UI KIT"
            title="Aurora primitives"
            sub="Every primitive in every state — dev-only review surface."
            status={{ label: "Live preview" }}
          />

          <Group title="Buttons">
            <Button variant="primary">Primary</Button>
            <Button variant="accent">Accent</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="primary" loading>Loading</Button>
            <Button variant="primary" disabled>Disabled</Button>
            <Button variant="accent" disabled>Disabled</Button>
          </Group>

          <Group title="Tags / Chips">
            <Tag variant="critical">Critical</Tag>
            <Tag variant="warning">Warning</Tag>
            <Tag variant="ok">OK</Tag>
            <Tag variant="info">Info</Tag>
          </Group>

          <Group title="Rings (threshold-coloured)">
            <Ring value={pct >= 72 ? 88 : 0} />
            <Ring value={pct >= 72 ? 58 : 0} />
            <Ring value={pct >= 72 ? 24 : 0} />
            <Ring value={0} label="0" />
          </Group>

          <Group title="Progress bar">
            <div style={{ flex: 1, minWidth: 260 }}><ProgressBar value={pct} /></div>
          </Group>

          <Group title="Skeleton">
            <div style={{ flex: 1, minWidth: 260, display: "grid", gap: 10 }}>
              <Skeleton h={18} w="60%" /><Skeleton h={14} /><Skeleton h={14} w="80%" />
            </div>
          </Group>

          <h2 className="au-kit-sec">Metrics (in a bento)</h2>
          <Bento>
            <Cell span={2} solid><Metric label="Mention rate" value="72%"
              delta={{ dir: "up", value: "+6" }} sub="across 3 engines" /></Cell>
            <Cell span={2} tint="mint"><Metric label="Citations" value="18"
              delta={{ dir: "down", value: "-2" }} sub="last 7 days" /></Cell>
            <Cell span={2} tint="lav"><Metric label="Prompts" value="24" sub="10 active" /></Cell>
            <Cell span={3} tint="peach"><Metric label="Rank" value="2nd" sub="of 5 tracked" /></Cell>
            <Cell span={3} tint="sky"><Metric label="Runs" value="146" sub="all-time" /></Cell>
          </Bento>

          <h2 className="au-kit-sec">Step list (idle / active / done)</h2>
          <Cell solid>
            <StepList steps={[
              { label: "Fetching page", state: "done", time: "0.4s" },
              { label: "Reading structured data", state: "done", time: "0.9s" },
              { label: "Asking the engines", state: "active", time: "…" },
              { label: "Scoring visibility", state: "idle" },
            ]} />
          </Cell>

          <h2 className="au-kit-sec">Engine cards (fixed colours + states)</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
            <EngineCard engine="openai" status="yes" statusLabel="3/3"
              footer={<>DM Mono footer · 1.2s</>}>Named your brand in every sample.</EngineCard>
            <EngineCard engine="anthropic" status="no" statusLabel="0/3"
              footer={<>DM Mono footer · 1.6s</>}>Recommended two competitors instead.</EngineCard>
            <EngineCard engine="perplexity" status="pending" statusLabel="…" />
            <EngineCard engine="gemini" status="yes" statusLabel="2/3"
              footer={<>DM Mono footer · 0.9s</>}>Cited your pricing page.</EngineCard>
          </div>

          <h2 className="au-kit-sec">Typing indicator</h2>
          <div className="au-kit-row"><TypingDots /></div>

          <h2 className="au-kit-sec">Engine colour map (verbatim from prototype)</h2>
          <div className="au-kit-row">
            {Object.entries(AURORA_ENGINES)
              .filter(([k]) => ["openai", "anthropic", "perplexity", "gemini"].includes(k))
              .map(([k, e]) => (
                <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <span className="au-jc-ic" style={{ background: e.color }}>{e.letter}</span>
                  <span style={{ fontSize: 13 }}>{e.name} <code style={{ opacity: .6 }}>({k})</code></span>
                </span>
              ))}
          </div>

          {/* ---- Signature viz 1: Podium ---- */}
          <h2 className="au-kit-sec">Podium — normal (You #2 of 5)</h2>
          <Cell solid><Podium entries={LB_NORMAL} /></Cell>
          <h2 className="au-kit-sec">Podium — ties (two at #1)</h2>
          <Cell solid><Podium entries={LB_TIES} /></Cell>
          <h2 className="au-kit-sec">Podium — single competitor</h2>
          <Cell solid><Podium entries={LB_ONE} /></Cell>
          <h2 className="au-kit-sec">Podium — You appended beyond top 5 (real rank #11, after a break)</h2>
          <Cell solid><Podium entries={LB_APPENDED} /></Cell>
          <h2 className="au-kit-sec">Podium — no competitor data yet</h2>
          <Cell solid><Podium entries={[]} /></Cell>

          {/* ---- Signature viz 2: Radar ---- */}
          <h2 className="au-kit-sec">Radar — 6 dimensions</h2>
          <Cell solid><Radar axes={RADAR_AXES} /></Cell>
          <h2 className="au-kit-sec">Radar — fewer than 3 dimensions (empty state)</h2>
          <Cell solid><Radar axes={RADAR_TWO} /></Cell>

          {/* ---- Signature viz 3: Prompt row list ---- */}
          <h2 className="au-kit-sec">Prompt rows (named / gap / null-rate)</h2>
          <PromptRowList prompts={PROMPTS} onSelect={() => {}} />
        </div>
      </Shell>
    </div>
  );
}
