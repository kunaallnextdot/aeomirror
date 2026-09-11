/* Public shared-report page (/r/:token) — MIGRATED to Aurora. Rendered WITHOUT the dashboard
   shell; works signed-out. useNoIndex (robots noindex) + data fetch are UNCHANGED — only the
   chrome + loading/error skin changed. Copy is verbatim. Renders ReportView in read-only mode
   (ReportView is already Aurora). */
import React, { useEffect, useState } from "react";
import { Radar, AlertTriangle } from "lucide-react";
import { getPublicReport, ScanError } from "../api.js";
import ReportView from "./ReportView.jsx";
import { Shell, Cell, Skeleton } from "./aurora.jsx";
import "./aurora.css";

function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex";
    document.head.appendChild(meta);
    return () => { try { document.head.removeChild(meta); } catch { /* already gone */ } };
  }, []);
}

export default function PublicReport({ token }) {
  useNoIndex();
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setReport(null); setError(null);
    getPublicReport(token)
      .then((r) => { if (alive) setReport(r); })
      .catch((e) => { if (alive) setError(e instanceof ScanError ? e.message : "Could not load this report."); });
    return () => { alive = false; };
  }, [token]);

  return (
    <div className="au-pub">
      <header className="au-pub-bar">
        <a className="au-pub-brand" href="/"><Radar size={16} /> Scanned with <b>&nbsp;AEOMirror</b></a>
      </header>

      <main className="au-pub-main">
        {error ? (
          <div className="aurora-screen"><Shell><Cell solid><div className="au-card-center">
            <div className="au-ill au-ill-bad"><AlertTriangle size={24} /></div>
            <div className="au-card-t">Report unavailable</div>
            <div className="au-card-s">{error}</div>
            <a className="au-btn au-accent" href="/">Go to AEOMirror</a>
          </div></Cell></Shell></div>
        ) : !report ? (
          <div className="aurora-screen"><Shell><div className="au-stack">
            <Cell solid><div style={{ display: "grid", gap: 10 }}><Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} /></div></Cell>
            <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
          </div></Shell></div>
        ) : (
          <ReportView readOnly report={report} />
        )}
      </main>

      <footer className="au-pub-foot">
        <a href="/">Get your own free AI-visibility report at <b>AEOMirror</b> →</a>
      </footer>
    </div>
  );
}
