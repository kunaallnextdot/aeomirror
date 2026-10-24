/* Public shared-report page (/r/:token) — MIGRATED to Spectrum. Rendered WITHOUT the dashboard
   shell; works signed-out. useNoIndex (robots noindex) + data fetch are UNCHANGED — only the
   chrome + loading/error skin changed. Copy is verbatim. Renders ReportView in read-only mode
   (ReportView itself is still on its own pre-Spectrum styling until its own migration pass —
   a bounded, known gap, same shape as every prior "chrome now, body later" split). */
import React, { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import Logo from "../shared/Logo.jsx";
import { getPublicReport, ScanError } from "../api.js";
import ReportView from "./ReportView.jsx";
import { Shell, Cell, Skeleton } from "../shared/spectrum.jsx";

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
    <div className="sp-pub">
      <header className="sp-pub-bar">
        <a className="sp-pub-brand" href="/">Scanned with <Logo variant="lockup" size={16} /></a>
      </header>

      <main className="sp-pub-main">
        {error ? (
          <div className="spectrum-screen"><Shell><Cell solid><div className="sp-card-center">
            <div className="sp-ill sp-ill-bad"><AlertTriangle size={24} /></div>
            <div className="sp-card-t">Report unavailable</div>
            <div className="sp-card-s">{error}</div>
            <a className="sp-btn sp-gradient" href="/">Go to AEOMirror</a>
          </div></Cell></Shell></div>
        ) : !report ? (
          <div className="spectrum-screen"><Shell><div className="sp-stack">
            <Cell solid><div style={{ display: "grid", gap: 10 }}><Skeleton w="45%" h={22} /><Skeleton w="70%" h={12} /></div></Cell>
            <Cell solid><div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} h={44} />)}</div></Cell>
          </div></Shell></div>
        ) : (
          <ReportView readOnly report={report} />
        )}
      </main>

      <footer className="sp-pub-foot">
        <a href="/">Get your own free AI-visibility report at <b>AEOMirror</b> →</a>
      </footer>
    </div>
  );
}
