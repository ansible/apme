import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createScan, listScans } from "../services/api";
import type { ScanListItem } from "../types/api";
import { StatusBadge } from "../components/StatusBadge";
import { NewScanModal } from "../components/NewScanModal";
import { timeAgo } from "../services/format";

export function DashboardPage() {
  const navigate = useNavigate();
  const [scans, setScans] = useState<ScanListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [scanning, setScanning] = useState(false);

  const load = () => {
    setLoading(true);
    listScans(1, 5)
      .then((data) => {
        setScans(data.items);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleNewScan = async (path: string) => {
    setScanning(true);
    try {
      const scan = await createScan(path);
      setShowModal(false);
      navigate(`/scans/${scan.id}`);
    } catch {
      alert("Scan failed. Is the gateway running?");
    } finally {
      setScanning(false);
    }
  };

  const failed = scans.filter((s) => s.status === "completed" && s.total_violations > 0).length;
  const passed = scans.filter((s) => s.status === "completed" && s.total_violations === 0).length;
  const totalSecrets = scans.reduce((sum, s) => sum + (s.secrets || 0), 0);

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">Dashboard</h1>
        <button className="apme-btn apme-btn-primary" onClick={() => setShowModal(true)}>
          + New Scan
        </button>
      </header>

      <div className="apme-cards-grid">
        <div className="apme-metric-card">
          <div className="apme-metric-value">{total}</div>
          <div className="apme-metric-label">Total Scans</div>
        </div>
        <div className="apme-metric-card">
          <div className="apme-metric-value warning">{failed}</div>
          <div className="apme-metric-label">With Issues</div>
        </div>
        <div className="apme-metric-card">
          <div className="apme-metric-value success">{passed}</div>
          <div className="apme-metric-label">Clean</div>
        </div>
        <div className="apme-metric-card">
          <div className="apme-metric-value error">{totalSecrets}</div>
          <div className="apme-metric-label">Secrets Leaked</div>
        </div>
      </div>

      <div className="apme-section-header">
        <h2 className="apme-section-title">Recent Scans</h2>
        <Link to="/scans" className="apme-link">View all</Link>
      </div>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : scans.length === 0 ? (
        <div className="apme-empty">No scans yet. Click "New Scan" to get started.</div>
      ) : (
        <div className="apme-table-container">
          <table className="apme-data-table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Source</th>
                <th>Status</th>
                <th>Secrets</th>
                <th>Error</th>
                <th>V.High</th>
                <th>High</th>
                <th>Med</th>
                <th>Warn</th>
                <th>Low</th>
                <th>V.Low</th>
                <th>Hint</th>
                <th>Fixed / Fixable</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((scan) => (
                <tr key={scan.id} onClick={() => navigate(`/scans/${scan.id}`)} style={{ cursor: "pointer" }}>
                  <td className="apme-target-path">{scan.project_path}</td>
                  <td>
                    <span className={`apme-badge ${scan.source === "fix" ? "passed" : "running"}`}>
                      {scan.source === "fix" ? "fix" : "scan"}
                    </span>
                  </td>
                  <td><StatusBadge status={scan.status} violations={scan.total_violations} /></td>
                  <td><span className="apme-count-critical">{scan.secrets || ""}</span></td>
                  <td><span className="apme-count-error">{scan.errors || ""}</span></td>
                  <td><span className="apme-count-very-high">{scan.very_high || ""}</span></td>
                  <td><span className="apme-count-high">{scan.high || ""}</span></td>
                  <td><span className="apme-count-medium">{scan.medium || ""}</span></td>
                  <td><span className="apme-count-warning">{scan.warnings || ""}</span></td>
                  <td><span className="apme-count-low">{scan.low || ""}</span></td>
                  <td><span className="apme-count-very-low">{scan.very_low || ""}</span></td>
                  <td><span className="apme-count-hint">{scan.hints || ""}</span></td>
                  <td>
                    {scan.source === "fix" && scan.fixed > 0
                      ? <span className="apme-count-success">{scan.fixed} fixed</span>
                      : scan.fixable > 0
                        ? <span className="apme-count-fixable">{scan.fixable} fixable</span>
                        : ""}
                  </td>
                  <td className="apme-time-ago">{timeAgo(scan.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <NewScanModal
          onSubmit={handleNewScan}
          onCancel={() => setShowModal(false)}
          loading={scanning}
        />
      )}
    </>
  );
}
