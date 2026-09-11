/* Dashboard Home — MIGRATED to Aurora. Data (GET /api/dashboard) and props are unchanged;
   only JSX + class names changed. `.aurora-screen`-scoped; charts use their `aurora` theme. */
import React from "react";
import { Layers, Gauge, TrendingUp, TrendingDown, Clock, AlertTriangle } from "lucide-react";
import { ScoreHistoryChart, ScoreDistributionChart, TopIssuesChart, CommonFailuresChart } from "./charts.jsx";
import { Shell, Cell, Ring, Button } from "./aurora.jsx";

function AuStat({ label, value, sub, icon: Icon }) {
  return (
    <Cell solid>
      <div className="au-statcard-l">{Icon && <Icon size={13} />} {label}</div>
      <div className="au-statcard-v">{value}</div>
      {sub && <div className="au-statcard-sub">{sub}</div>}
    </Cell>
  );
}

export default function DashboardHome({ data, onOpenLatest }) {
  const d = data || {};
  const latest = d.latest_scan;
  const mci = d.most_common_issue;
  return (
    <div className="aurora-screen">
      <Shell>
        <div className="au-stats">
          <AuStat label="Total scans" value={d.total_scans ?? 0} icon={Layers} />
          <AuStat label="Average AI score" value={d.average_score ?? "-"} sub="across all scans" icon={Gauge} />
          <AuStat label="Highest score" value={d.highest_score ?? "-"} icon={TrendingUp} />
          <AuStat label="Lowest score" value={d.lowest_score ?? "-"} icon={TrendingDown} />
        </div>

        <div className="au-grid2" style={{ marginBottom: 16 }}>
          <Cell solid style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <span role="img" aria-label={`Latest score ${latest?.overall_score ?? "not available"}`}><Ring value={latest?.overall_score} size={58} stroke={6} /></span>
            <div style={{ minWidth: 0 }}>
              <div className="au-statcard-l"><Clock size={13} /> Latest scan</div>
              {latest ? (
                <>
                  <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--au-ink)" }}>{latest.domain}</div>
                  <Button variant="ghost" onClick={() => onOpenLatest?.(latest.id)}>View report</Button>
                </>
              ) : <div className="au-dim" style={{ marginTop: 6 }}>No scans yet</div>}
            </div>
          </Cell>
          <Cell solid>
            <div className="au-statcard-l"><AlertTriangle size={13} /> Most common issue</div>
            {mci ? (
              <>
                <div style={{ fontSize: 14.5, fontWeight: 500, margin: "10px 0 6px", lineHeight: 1.4, color: "var(--au-ink)" }}>{mci.issue}</div>
                <div className="au-dim" style={{ fontSize: 12 }}>seen in {mci.count} scan{mci.count === 1 ? "" : "s"}</div>
              </>
            ) : <div className="au-dim" style={{ marginTop: 10 }}>No recurring issues 🎉</div>}
          </Cell>
        </div>

        <Cell solid style={{ marginBottom: 16 }}>
          <div className="au-panel-h">Score trend <span className="au-sub">overall AI visibility over time</span></div>
          <ScoreHistoryChart trend={d.score_trend} aurora />
        </Cell>

        <div className="au-grid2">
          <Cell solid>
            <div className="au-panel-h">Score distribution</div>
            <ScoreDistributionChart distribution={d.score_distribution} aurora />
          </Cell>
          <Cell solid>
            <div className="au-panel-h">Top issue categories <span className="au-sub">most-failed signals</span></div>
            <TopIssuesChart categories={d.top_issue_categories} aurora />
          </Cell>
        </div>

        <Cell solid style={{ marginTop: 16 }}>
          <div className="au-panel-h">Most common failures <span className="au-sub">recurring issues across your scans</span></div>
          <CommonFailuresChart failures={d.common_failures} aurora />
        </Cell>
      </Shell>
    </div>
  );
}
