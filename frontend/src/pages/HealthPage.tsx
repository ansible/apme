import { useEffect, useState } from "react";
import { getHealth } from "../services/api";
import type { HealthStatus } from "../types/api";

export function HealthPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const isOk = (status: string) => status === "ok";
  const statusColor = (status: string) => isOk(status) ? "var(--apme-green)" : "var(--apme-sev-critical)";
  const statusIcon = (status: string) => isOk(status) ? "\u2714" : "\u2718";

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">System Health</h1>
        <button className="apme-btn apme-btn-secondary" onClick={load}>
          Refresh
        </button>
      </header>

      {loading ? (
        <div className="apme-empty">Checking health...</div>
      ) : !health ? (
        <div className="apme-empty">Unable to reach gateway.</div>
      ) : (
        <div className="apme-table-container">
          <table className="apme-data-table">
            <thead>
              <tr>
                <th>Component</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Gateway</td>
                <td style={{ color: statusColor(health.status) }}>
                  {statusIcon(health.status)} {health.status}
                </td>
              </tr>
              <tr>
                <td>Database</td>
                <td style={{ color: statusColor(health.database) }}>
                  {statusIcon(health.database)} {health.database}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
