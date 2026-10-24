/* Dashboard Home — MIGRATED to Spectrum. Data (GET /api/dashboard) and props are unchanged;
   only JSX + class names changed. `.spectrum-screen`-scoped; charts use their `spectrum` theme. */
import React from "react";
import { Layers, Gauge, TrendingUp, TrendingDown, Clock, AlertTriangle } from "lucide-react";
import { ScoreHistoryChart, ScoreDistributionChart, TopIssuesChart, CommonFailuresChart } from "./charts.jsx";
import { Shell, Cell, Ring, Button } from "../shared/spectrum.jsx";

function SpStat({ label, value, sub, icon: Icon }) {
  return (
    <Cell solid>
      <div className="sp-statcard-l">{Icon && <Icon size={13} />} {label}</div>
      <div className="sp-statcard-v">{value}</div>
      {sub && <div className="sp-statcard-sub">{sub}</div>}
    </Cell>
  );
}

export default function DashboardHome({ data, onOpenLatest }) {
  const d = data || {};
  const latest = d.latest_scan;
  const mci = d.most_common_issue;
  return (
    <div className="spectrum-screen">
      <Shell>
        <div className="sp-stats">
          <SpStat label="Total scans" value={d.total_scans ?? 0} icon={Layers} />
          <SpStat label="Average AI score" value={d.average_score ?? "-"} sub="across all scans" icon={Gauge} />
          <SpStat label="Highest score" value={d.highest_score ?? "-"} icon={TrendingUp} />
          <SpStat label="Lowest score" value={d.lowest_score ?? "-"} icon={TrendingDown} />
        </div>

        <div className="sp-grid2" style={{ marginBottom: 16 }}>
          <Cell solid style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <span role="img" aria-label={`Latest score ${latest?.overall_score ?? "not available"}`}><Ring value={latest?.overall_score} size={58} stroke={6} /></span>
            <div style={{ minWidth: 0 }}>
              <div className="sp-statcard-l"><Clock size={13} /> Latest scan</div>
              {latest ? (
                <>
                  <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--sp-ink)" }}>{latest.domain}</div>
                  <Button variant="ghost" onClick={() => onOpenLatest?.(latest.id)}>View report</Button>
                </>
              ) : <div className="sp-dim" style={{ marginTop: 6 }}>No scans yet</div>}
            </div>
          </Cell>
          <Cell solid>
            <div className="sp-statcard-l"><AlertTriangle size={13} /> Most common problem</div>
            {mci ? (
              <>
                <div style={{ fontSize: 14.5, fontWeight: 500, margin: "10px 0 6px", lineHeight: 1.4, color: "var(--sp-ink)" }}>{mci.issue}</div>
                <div className="sp-dim" style={{ fontSize: 12 }}>seen in {mci.count} scan{mci.count === 1 ? "" : "s"}</div>
              </>
            ) : <div className="sp-dim" style={{ marginTop: 10 }}>No recurring problems 🎉</div>}
          </Cell>
        </div>

        <Cell solid style={{ marginBottom: 16 }}>
          <div className="sp-panel-h">Score trend <span className="sp-sub">overall AI visibility over time</span></div>
          <ScoreHistoryChart trend={d.score_trend} spectrum />
        </Cell>

        <div className="sp-grid2">
          <Cell solid>
            <div className="sp-panel-h">Score distribution</div>
            <ScoreDistributionChart distribution={d.score_distribution} spectrum />
          </Cell>
          <Cell solid>
            <div className="sp-panel-h">Top problem categories <span className="sp-sub">most-failed checks</span></div>
            <TopIssuesChart categories={d.top_issue_categories} spectrum />
          </Cell>
        </div>

        <Cell solid style={{ marginTop: 16 }}>
          <div className="sp-panel-h">Most common failures <span className="sp-sub">recurring problems across your scans</span></div>
          <CommonFailuresChart failures={d.common_failures} spectrum />
        </Cell>
      </Shell>
    </div>
  );
}
