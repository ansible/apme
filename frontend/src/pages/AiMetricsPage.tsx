import { useEffect, useState } from "react";
import { getAiAcceptance } from "../services/api";
import type { AiAcceptanceEntry } from "../types/api";

export function AiMetricsPage() {
  const [data, setData] = useState<AiAcceptanceEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAiAcceptance()
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">AI Metrics</h1>
        <p style={{ color: "var(--apme-text-muted)", margin: 0, fontSize: 14 }}>
          Proposal acceptance rates by rule
        </p>
      </header>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : data.length === 0 ? (
        <div className="apme-empty">No AI proposal data yet. Run a fix session with AI escalation to see results.</div>
      ) : (
        <div className="apme-table-container">
          <table className="apme-data-table">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Approved</th>
                <th>Rejected</th>
                <th>Pending</th>
                <th>Avg Confidence</th>
                <th>Acceptance Rate</th>
              </tr>
            </thead>
            <tbody>
              {data.map((entry) => {
                const total = entry.approved + entry.rejected + entry.pending;
                const rate = total > 0 ? Math.round((entry.approved / total) * 100) : 0;
                return (
                  <tr key={entry.rule_id}>
                    <td className="apme-rule-id">{entry.rule_id}</td>
                    <td><span className="apme-count-success">{entry.approved}</span></td>
                    <td><span className="apme-count-error">{entry.rejected}</span></td>
                    <td>{entry.pending}</td>
                    <td>{(entry.avg_confidence * 100).toFixed(0)}%</td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{ flex: 1, background: "var(--apme-bg-tertiary)", borderRadius: 4, height: 16, maxWidth: 120 }}>
                          <div style={{
                            width: `${rate}%`,
                            background: rate > 70 ? "var(--apme-green)" : rate > 40 ? "var(--apme-sev-medium)" : "var(--apme-sev-error)",
                            height: "100%",
                            borderRadius: 4,
                          }} />
                        </div>
                        <span style={{ fontSize: 12, color: "var(--apme-text-secondary)" }}>{rate}%</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
