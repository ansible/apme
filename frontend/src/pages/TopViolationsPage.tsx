import { useEffect, useState } from "react";
import { getTopViolations } from "../services/api";
import type { TopViolation } from "../types/api";

export function TopViolationsPage() {
  const [data, setData] = useState<TopViolation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getTopViolations(30)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const maxCount = data.length > 0 ? data[0]!.count : 1;

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">Top Violations</h1>
      </header>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : data.length === 0 ? (
        <div className="apme-empty">No violation data yet.</div>
      ) : (
        <div className="apme-table-container">
          {data.map((entry) => (
            <div key={entry.rule_id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 20px", borderBottom: "1px solid var(--apme-border)" }}>
              <span className="apme-rule-id" style={{ minWidth: 60 }}>{entry.rule_id}</span>
              <div style={{ flex: 1, background: "var(--apme-bg-tertiary)", borderRadius: 4, height: 20 }}>
                <div style={{
                  width: `${(entry.count / maxCount) * 100}%`,
                  background: "var(--apme-accent)",
                  height: "100%",
                  borderRadius: 4,
                  minWidth: 2,
                }} />
              </div>
              <span style={{ minWidth: 40, textAlign: "right", fontSize: 13, color: "var(--apme-text-secondary)" }}>
                {entry.count}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
