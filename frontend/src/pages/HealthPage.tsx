import { useEffect, useState } from "react";
import { getHealth } from "../services/api";
import type { HealthOut } from "../types/api";

export function HealthPage() {
  const [health, setHealth] = useState<HealthOut | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const isOk = (status: string) => status.startsWith("ok");

  const statusColor = (status: string) =>
    isOk(status) ? "var(--apme-green)" : "var(--apme-red)";

  const statusIcon = (status: string) =>
    isOk(status) ? "\u2714" : "\u2718";

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
                <th>Service</th>
                <th>Status</th>
                <th>Address</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Gateway</td>
                <td style={{ color: statusColor(health.gateway) }}>
                  {statusIcon(health.gateway)} {health.gateway}
                </td>
                <td className="apme-time-ago">localhost:8080</td>
              </tr>
              {health.primary && (
                <tr>
                  <td>Primary</td>
                  <td style={{ color: statusColor(health.primary.status) }}>
                    {statusIcon(health.primary.status)} {health.primary.status}
                  </td>
                  <td className="apme-time-ago">{health.primary.address}</td>
                </tr>
              )}
              {health.downstream.map((svc) => (
                <tr key={svc.name}>
                  <td>{svc.name}</td>
                  <td style={{ color: statusColor(svc.status) }}>
                    {statusIcon(svc.status)} {svc.status}
                  </td>
                  <td className="apme-time-ago">{svc.address}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
