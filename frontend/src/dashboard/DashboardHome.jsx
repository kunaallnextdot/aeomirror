/* Dashboard Home (Phase 4): headline stats + score trend / distribution / top
   issues charts. All data comes from GET /api/dashboard. */
import React from "react";
import { Layers, Gauge, TrendingUp, TrendingDown, Clock, AlertTriangle } from "lucide-react";
import { StatCard, ScoreRing, fmtDate } from "./ui.jsx";
import { ScoreHistoryChart, ScoreDistributionChart, TopIssuesChart, CommonFailuresChart } from "./charts.jsx";

export default function DashboardHome({ data, onOpenLatest }) {
  const d = data || {};
  const latest = d.latest_scan;
  const mci = d.most_common_issue;
  return (
    <div>
      <div className="d-stats">
        <StatCard label="Total scans" value={d.total_scans ?? 0} icon={Layers} />
        <StatCard label="Average AI score" value={d.average_score ?? "-"} sub="across all scans" icon={Gauge} />
        <StatCard label="Highest score" value={d.highest_score ?? "-"} icon={TrendingUp} />
        <StatCard label="Lowest score" value={d.lowest_score ?? "-"} icon={TrendingDown} />
      </div>

      <div className="d-grid d-grid-2" style={{ marginBottom: 18 }}>
        <div className="d-card" style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <ScoreRing value={latest?.overall_score} size={58} stroke={6} />
          <div style={{ minWidth: 0 }}>
            <div className="d-stat-label"><Clock size={13} /> Latest scan</div>
            {latest ? (
              <>
                <div style={{ fontSize: 16, fontWeight: 600, margin: "4px 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{latest.domain}</div>
                <button className="d-iconbtn" onClick={() => onOpenLatest?.(latest.id)}>View report</button>
              </>
            ) : <div className="d-dim" style={{ marginTop: 6 }}>No scans yet</div>}
          </div>
        </div>
        <div className="d-card">
          <div className="d-stat-label"><AlertTriangle size={13} /> Most common issue</div>
          {mci ? (
            <>
              <div style={{ fontSize: 14.5, fontWeight: 500, margin: "10px 0 6px", lineHeight: 1.4 }}>{mci.issue}</div>
              <div className="d-dim" style={{ fontSize: 12 }}>seen in {mci.count} scan{mci.count === 1 ? "" : "s"}</div>
            </>
          ) : <div className="d-dim" style={{ marginTop: 10 }}>No recurring issues 🎉</div>}
        </div>
      </div>

      <div className="d-panel" style={{ marginBottom: 16 }}>
        <div className="d-panel-h">Score trend <span className="sub">overall AI visibility over time</span></div>
        <ScoreHistoryChart trend={d.score_trend} />
      </div>

      <div className="d-grid d-grid-2">
        <div className="d-panel">
          <div className="d-panel-h">Score distribution</div>
          <ScoreDistributionChart distribution={d.score_distribution} />
        </div>
        <div className="d-panel">
          <div className="d-panel-h">Top issue categories <span className="sub">most-failed signals</span></div>
          <TopIssuesChart categories={d.top_issue_categories} />
        </div>
      </div>

      <div className="d-panel" style={{ marginTop: 16 }}>
        <div className="d-panel-h">Most common failures <span className="sub">recurring issues across your scans</span></div>
        <CommonFailuresChart failures={d.common_failures} />
      </div>
    </div>
  );
}
