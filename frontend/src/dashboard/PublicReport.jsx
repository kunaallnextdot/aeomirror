/* Public shared-report page (/r/:token). Rendered WITHOUT the dashboard shell, sidebar
   or account nav, and works for a signed-out visitor. It fetches the report through the
   plain (non-auth) getPublicReport, injects <meta name="robots" content="noindex"> (the
   edge also sends X-Robots-Tag on /r/*), and renders ReportView in read-only mode. The
   "Scanned with AEOMirror" marks link back to the homepage — the point of the feature. */
import React, { useEffect, useState } from "react";
import { Radar, AlertTriangle } from "lucide-react";
import { getPublicReport, ScanError } from "../api.js";
import ReportView from "./ReportView.jsx";
import { TableSkeleton } from "./ui.jsx";

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
    <div className="pub">
      <header className="pub-bar">
        <a className="pub-brand" href="/"><Radar size={16} /> Scanned with <b>AEOMirror</b></a>
      </header>

      <main className="pub-main">
        {error ? (
          <div className="d-panel" style={{ textAlign: "center", padding: 40 }}>
            <div className="d-mini-empty-ill" style={{ margin: "0 auto 14px", background: "rgba(229,97,91,.12)", color: "var(--bad)" }}>
              <AlertTriangle size={24} />
            </div>
            <div style={{ fontSize: 17, fontWeight: 700, fontFamily: "'Hanken Grotesk'" }}>Report unavailable</div>
            <div className="d-dim" style={{ marginTop: 6, fontSize: 13 }}>{error}</div>
            <a className="btn btn-primary" style={{ display: "inline-flex", marginTop: 16 }} href="/">Go to AEOMirror</a>
          </div>
        ) : !report ? (
          <TableSkeleton rows={5} />
        ) : (
          <ReportView readOnly report={report} />
        )}
      </main>

      <footer className="pub-foot">
        <a href="/">Get your own free AI-visibility report at <b>AEOMirror</b> →</a>
      </footer>
    </div>
  );
}
