/* DEV-ONLY Spectrum UI kit — renders every Phase-3 primitive in every state for visual
   review. Reachable at /ui-kit-spectrum ONLY in development (gated on import.meta.env.DEV
   and lazy-loaded in App.jsx, so it is dead-code-eliminated from production builds).

   Why a separate path from the existing /ui-kit (Aurora's): that route already exists and
   is taken. Reusing it would also mean rendering alongside Aurora's page, which currently
   throws on mount in dev — it imports Podium/Radar/PromptRowList from aurora.jsx, none of
   which are actually exported there (pre-existing, unrelated to this work, not fixed here).
   This route is fully independent of that file and renders cleanly on its own.

   REMOVAL (final cleanup phase): delete this file, then remove the `SpectrumUiKit` lazy
   import and the `import.meta.env.DEV && <Route path="/ui-kit-spectrum" .../>` line from
   src/App.jsx. Do NOT delete spectrum.jsx/spectrum.css — those are the real primitives,
   consumed by actual screens once their migration phase opts in; only this review page
   and its route are temporary. Nothing else references this file. */
import React, { useEffect, useState } from "react";
import {
  Shell, TopNav, PageHead, Bento, Cell, Metric, Tag, Button, Ring, ProgressBar,
  Skeleton, StepList, EngineCard, TypingDots, SPECTRUM_ENGINES,
} from "./spectrum.jsx";

function Group({ title, children }) {
  return (<>
    <h2 className="sp-kit-sec">{title}</h2>
    <div className="sp-kit-row">{children}</div>
  </>);
}

export default function SpectrumUiKit() {
  // Drive the animated primitives (progress + ring) so both zero and filled states are seen.
  const [pct, setPct] = useState(0);
  useEffect(() => { const t = setTimeout(() => setPct(72), 120); return () => clearTimeout(t); }, []);

  const navItems = [
    { label: "Overview", active: true }, { label: "Answers" },
    { label: "Monitoring" }, { label: "Report" },
  ];

  return (
    <div className="sp-kit">
      <Shell>
        <TopNav
          items={navItems}
          credits={{ used: 128, total: 500 }}
          cta={{ label: "Run a scan" }}
          avatar="KS"
        />
        <div className="sp-kit-body">
          <PageHead
            eyebrow="AEOMIRROR / UI KIT — SPECTRUM"
            title="Spectrum primitives"
            sub="Every primitive in every state — dev-only review surface."
            status={{ label: "Live preview" }}
          />

          <Group title="Buttons">
            <Button variant="ink">Ink</Button>
            <Button variant="gradient">Gradient</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="ink" loading>Loading</Button>
            <Button variant="ink" disabled>Disabled</Button>
            <Button variant="gradient" disabled>Disabled</Button>
          </Group>

          <Group title="Tags / Chips (healthy / warning / needs-work / neutral — no red/amber/green)">
            <Tag variant="healthy">Healthy</Tag>
            <Tag variant="warning">Warning</Tag>
            <Tag variant="needs-work">Needs work</Tag>
            <Tag variant="neutral">Neutral</Tag>
          </Group>

          <Group title="Rings (threshold-coloured, drawn from the ramp only)">
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

          <h2 className="sp-kit-sec">Metrics (in a bento, incl. gradient text-fill variant)</h2>
          <Bento>
            <Cell span={2} solid><Metric label="Mention rate" value="72%"
              delta={{ dir: "up", value: "+6" }} sub="across 3 engines" /></Cell>
            <Cell span={2} tint="cyan"><Metric label="Citations" value="18"
              delta={{ dir: "down", value: "-2" }} sub="last 7 days" /></Cell>
            <Cell span={2} tint="violet"><Metric label="Prompts" value="24" sub="10 active" /></Cell>
            <Cell span={3} tint="indigo"><Metric label="Rank" value="2nd" sub="of 5 tracked" /></Cell>
            <Cell span={3} tint="lav" solid><Metric label="Visibility score" value="84" gradient sub="gradient text-fill variant" /></Cell>
          </Bento>

          <h2 className="sp-kit-sec">Step list (idle / active / done)</h2>
          <Cell solid>
            <StepList steps={[
              { label: "Fetching page", state: "done", time: "0.4s" },
              { label: "Reading structured data", state: "done", time: "0.9s" },
              { label: "Asking the engines", state: "active", time: "…" },
              { label: "Scoring visibility", state: "idle" },
            ]} />
          </Cell>

          <h2 className="sp-kit-sec">Engine cards (fixed colours, drawn from the ramp + states)</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
            <EngineCard engine="openai" status="yes" statusLabel="3/3"
              footer={<>DM Mono footer · 1.2s</>}>Named your brand in every sample.</EngineCard>
            <EngineCard engine="anthropic" status="no" statusLabel="0/3"
              footer={<>DM Mono footer · 1.6s</>}>Recommended two competitors instead.</EngineCard>
            <EngineCard engine="perplexity" status="pending" statusLabel="…" />
            <EngineCard engine="gemini" status="yes" statusLabel="2/3"
              footer={<>DM Mono footer · 0.9s</>}>Cited your pricing page.</EngineCard>
          </div>

          <h2 className="sp-kit-sec">Typing indicator</h2>
          <div className="sp-kit-row"><TypingDots /></div>

          <h2 className="sp-kit-sec">Engine colour map (fixed, drawn from the ramp only)</h2>
          <div className="sp-kit-row">
            {Object.entries(SPECTRUM_ENGINES)
              .filter(([k]) => ["openai", "anthropic", "perplexity", "gemini"].includes(k))
              .map(([k, e]) => (
                <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <span className="sp-jc-ic" style={{ background: e.color }}>{e.letter}</span>
                  <span style={{ fontSize: 13 }}>{e.name} <code style={{ opacity: .6 }}>({k})</code></span>
                </span>
              ))}
          </div>
        </div>
      </Shell>
    </div>
  );
}
