import { useEffect, useState } from "react";
import { getFixRates } from "../services/api";
import type { FixRateEntry } from "../types/api";

export function FixTrackerPage() {
  const [data, setData] = useState<FixRateEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getFixRates(30)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const maxCount = data.length > 0 ? data[0]!.fix_count : 1;

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">Fix Tracker</h1>
        <p style={{ color: "var(--apme-text-muted)", margin: 0, fontSize: 14 }}>
          Most frequently addressed rules in fix sessions
        </p>
      </header>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : data.length === 0 ? (
        <div className="apme-empty">No fix data yet. Run a fix session to see results.</div>
      ) : (
        <div className="apme-table-container">
          {data.map((entry) => (
            <div key={entry.rule_id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 20px", borderBottom: "1px solid var(--apme-border)" }}>
              <span className="apme-rule-id" style={{ minWidth: 60 }}>{entry.rule_id}</span>
              <div style={{ flex: 1, background: "var(--apme-bg-tertiary)", borderRadius: 4, height: 20 }}>
                <div style={{
                  width: `${(entry.fix_count / maxCount) * 100}%`,
                  background: "var(--apme-green)",
                  height: "100%",
                  borderRadius: 4,
                  minWidth: 2,
                }} />
              </div>
              <span style={{ minWidth: 40, textAlign: "right", fontSize: 13, color: "var(--apme-text-secondary)" }}>
                {entry.fix_count}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
