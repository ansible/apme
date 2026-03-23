import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { listScans } from "../services/api";
import type { ScanSummary } from "../types/api";
import { StatusBadge } from "../components/StatusBadge";
import { timeAgo } from "../services/format";

const PAGE_SIZE = 20;

export function ScansPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sessionFilter = searchParams.get("session_id") ?? undefined;
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    listScans(PAGE_SIZE, offset, sessionFilter)
      .then((data) => {
        setScans(data.items);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [offset, sessionFilter]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <>
      <header className="apme-page-header">
        <h1 className="apme-page-title">All Scans</h1>
      </header>

      {loading ? (
        <div className="apme-empty">Loading...</div>
      ) : scans.length === 0 ? (
        <div className="apme-empty">No scans recorded.</div>
      ) : (
        <div className="apme-table-container">
          <table className="apme-data-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Source</th>
                <th>Type</th>
                <th>Status</th>
                <th>Violations</th>
                <th>Auto-Fix</th>
                <th>AI</th>
                <th>Manual</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {scans.map((scan) => (
                <tr key={scan.scan_id} onClick={() => navigate(`/scans/${scan.scan_id}`)} style={{ cursor: "pointer" }}>
                  <td className="apme-target-path">{scan.project_path}</td>
                  <td><span className="apme-badge running">{scan.source}</span></td>
                  <td>
                    <span className={`apme-badge ${scan.scan_type === "fix" ? "passed" : "running"}`}>
                      {scan.scan_type}
                    </span>
                  </td>
                  <td><StatusBadge violations={scan.total_violations} scanType={scan.scan_type} /></td>
                  <td>{scan.total_violations}</td>
                  <td><span className="apme-count-success">{scan.auto_fixable || ""}</span></td>
                  <td>{scan.ai_candidate || ""}</td>
                  <td><span className="apme-count-error">{scan.manual_review || ""}</span></td>
                  <td className="apme-time-ago">{timeAgo(scan.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {totalPages > 1 && (
            <div className="apme-pagination">
              <button className="apme-btn apme-btn-secondary" disabled={offset <= 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>
                Previous
              </button>
              <span>Page {currentPage} of {totalPages}</span>
              <button className="apme-btn apme-btn-secondary" disabled={currentPage >= totalPages} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
                Next
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
