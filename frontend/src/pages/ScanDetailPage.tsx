import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getScan } from "../services/api";
import type { ScanDetail, ViolationDetail } from "../types/api";

function groupByFile(violations: ViolationDetail[]): Map<string, ViolationDetail[]> {
  const map = new Map<string, ViolationDetail[]>();
  for (const v of violations) {
    const key = v.file || "(unknown)";
    const arr = map.get(key) ?? [];
    arr.push(v);
    map.set(key, arr);
  }
  return map;
}

function severityClass(level: string, ruleId?: string): string {
  if (ruleId?.startsWith("SEC")) return "critical";
  const l = level.toLowerCase();
  if (l === "fatal") return "critical";
  if (l === "error") return "error";
  if (l === "very_high") return "very-high";
  if (l === "high") return "high";
  if (l === "medium") return "medium";
  if (["warning", "warn"].includes(l)) return "warning";
  if (l === "low") return "low";
  if (["very_low", "info"].includes(l)) return "very-low";
  return "hint";
}

function severityLabel(level: string, ruleId?: string): string {
  if (ruleId?.startsWith("SEC")) return "CRITICAL";
  const l = level.toLowerCase();
  if (l === "fatal") return "FATAL";
  if (l === "error") return "ERROR";
  if (l === "very_high") return "VERY HIGH";
  if (l === "high") return "HIGH";
  if (l === "medium") return "MEDIUM";
  if (["warning", "warn"].includes(l)) return "WARN";
  if (l === "low") return "LOW";
  if (["very_low", "info"].includes(l)) return "VERY LOW";
  return "HINT";
}

function severityOrder(cls: string): number {
  const order: Record<string, number> = {
    critical: 0, error: 1, "very-high": 2, high: 3,
    medium: 4, warning: 5, low: 6, "very-low": 7, hint: 8,
  };
  return order[cls] ?? 9;
}

function tierLabel(rc: number): string {
  if (rc === 1) return "Auto-Fix";
  if (rc === 2) return "AI";
  if (rc === 3) return "Manual";
  return "";
}

export function ScanDetailPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [scan, setScan] = useState<ScanDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!scanId) return;
    setLoading(true);
    getScan(scanId)
      .then(setScan)
      .catch(() => setScan(null))
      .finally(() => setLoading(false));
  }, [scanId]);

  const groups = useMemo(() => (scan ? groupByFile(scan.violations) : new Map()), [scan]);

  if (loading) return <div className="apme-empty">Loading...</div>;
  if (!scan) return <div className="apme-empty">Scan not found.</div>;

  const expandAll = () => setExpanded(new Set(groups.keys()));
  const collapseAll = () => setExpanded(new Set());

  return (
    <>
      <nav className="apme-breadcrumb">
        <Link to="/scans">All Scans</Link>
        <span className="apme-breadcrumb-sep">/</span>
        <span>{scan.project_path}</span>
      </nav>

      <header className="apme-page-header">
        <div>
          <h1 className="apme-page-title" style={{ fontFamily: "var(--pf-v5-global--FontFamily--monospace, monospace)" }}>
            {scan.project_path}
          </h1>
          <p style={{ color: "var(--apme-text-muted)", fontSize: 14, margin: 0 }}>
            <span className={`apme-badge ${scan.scan_type === "fix" ? "passed" : "running"}`} style={{ marginRight: 8 }}>
              {scan.scan_type}
            </span>
            {new Date(scan.created_at).toLocaleString()}
          </p>
        </div>
      </header>

      <div className="apme-summary-card">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className={`apme-status-icon ${scan.total_violations > 0 ? "failed" : "passed"}`}>
            {scan.total_violations > 0 ? "\u2717" : "\u2713"}
          </div>
          <span style={{ fontSize: 20, fontWeight: 600, color: scan.total_violations > 0 ? "var(--apme-sev-critical)" : "var(--apme-green)" }}>
            {scan.total_violations > 0 ? `${scan.total_violations} VIOLATIONS` : "CLEAN"}
          </span>
        </div>
        <div className="apme-summary-counts">
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-green)" }}>{scan.auto_fixable}</div>
            <div className="apme-count-box-label">Auto-Fix</div>
          </div>
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-sev-medium)" }}>{scan.ai_candidate}</div>
            <div className="apme-count-box-label">AI</div>
          </div>
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-sev-error)" }}>{scan.manual_review}</div>
            <div className="apme-count-box-label">Manual</div>
          </div>
        </div>
      </div>

      {/* Pipeline logs */}
      {scan.logs.length > 0 && (
        <div className="apme-section-header" style={{ marginTop: 24 }}>
          <h2 className="apme-section-title">Pipeline Log</h2>
        </div>
      )}
      {scan.logs.length > 0 && (
        <div className="apme-table-container" style={{ marginBottom: 24 }}>
          <table className="apme-data-table">
            <thead>
              <tr><th>Phase</th><th>Message</th></tr>
            </thead>
            <tbody>
              {scan.logs.map((lg) => (
                <tr key={lg.id}>
                  <td><span className="apme-badge running">{lg.phase}</span></td>
                  <td>{lg.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Violations by file */}
      <div className="apme-violations-section">
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--apme-border)", display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: "auto" }}>
            Violations by File ({scan.violations.length})
          </span>
          <button className="apme-btn apme-btn-secondary" onClick={expandAll} style={{ fontSize: 12, padding: "4px 10px" }}>
            Expand All
          </button>
          <button className="apme-btn apme-btn-secondary" onClick={collapseAll} style={{ fontSize: 12, padding: "4px 10px" }}>
            Collapse All
          </button>
        </div>

        {groups.size === 0 ? (
          <div className="apme-empty">No violations found.</div>
        ) : (
          Array.from(groups.entries()).map(([file, violations]) => (
            <div className="apme-file-group" key={file}>
              <div className="apme-file-header" onClick={() => {
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(file)) next.delete(file);
                  else next.add(file);
                  return next;
                });
              }}>
                <span style={{ color: "var(--apme-text-dimmed)" }}>{expanded.has(file) ? "\u25BC" : "\u25B6"}</span>
                <span className="apme-file-name">{file}</span>
                <span className="apme-file-count">{violations.length} issues</span>
              </div>
              {expanded.has(file) &&
                violations
                  .sort((a: ViolationDetail, b: ViolationDetail) =>
                    severityOrder(severityClass(a.level, a.rule_id)) - severityOrder(severityClass(b.level, b.rule_id)),
                  )
                  .map((v: ViolationDetail) => (
                  <div className="apme-violation-item" key={v.id}>
                    <span className={`apme-severity ${severityClass(v.level, v.rule_id)}`}>
                      {severityLabel(v.level, v.rule_id)}
                    </span>
                    <span className="apme-rule-id">{v.rule_id}</span>
                    {v.remediation_class > 0 && (
                      <span className="apme-badge running" style={{ fontSize: 10 }}>{tierLabel(v.remediation_class)}</span>
                    )}
                    {v.line != null && (
                      <span className="apme-line-number" title={`Line ${v.line}`}>
                        Line {v.line}
                      </span>
                    )}
                    <div className="apme-violation-message">{v.message}</div>
                  </div>
                ))}
            </div>
          ))
        )}
      </div>
    </>
  );
}
