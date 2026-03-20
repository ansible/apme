import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getScan } from "../services/api";
import type { ScanOut, ViolationOut } from "../types/api";

function groupByFile(violations: ViolationOut[]): Map<string, ViolationOut[]> {
  const map = new Map<string, ViolationOut[]>();
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
  if (["very_high", "high", "error", "fatal"].includes(l)) return "error";
  if (["medium", "low", "warning", "warn"].includes(l)) return "warning";
  if (["very_low", "info"].includes(l)) return "info";
  return "hint";
}

function severityLabel(level: string, ruleId?: string): string {
  if (ruleId?.startsWith("SEC")) return "CRITICAL";
  const l = level.toLowerCase();
  if (["very_high", "high", "error", "fatal"].includes(l)) return "ERROR";
  if (["medium", "low", "warning", "warn"].includes(l)) return "WARN";
  if (["very_low", "info"].includes(l)) return "INFO";
  return "HINT";
}

export function ScanDetailPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [scan, setScan] = useState<ScanOut | null>(null);
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

  if (loading) return <div className="apme-empty">Loading...</div>;
  if (!scan) return <div className="apme-empty">Scan not found.</div>;

  const groups = groupByFile(scan.violations);
  const secrets = scan.violations.filter((v) => v.rule_id.startsWith("SEC")).length;
  const errors = scan.violations.filter((v) => severityClass(v.level, v.rule_id) === "error").length;
  const warnings = scan.violations.filter((v) => severityClass(v.level, v.rule_id) === "warning").length;
  const infos = scan.violations.filter((v) => severityClass(v.level, v.rule_id) === "info").length;
  const hints = scan.violations.length - secrets - errors - warnings - infos;
  const hasFailed = errors > 0 || secrets > 0;

  const toggleFile = (file: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  };

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
            Scanned {new Date(scan.created_at).toLocaleString()}
            {scan.diagnostics && typeof scan.diagnostics === "object" && "total_ms" in scan.diagnostics
              ? ` \u2022 Duration: ${(scan.diagnostics.total_ms as number).toFixed(0)}ms`
              : ""}
          </p>
        </div>
      </header>

      <div className="apme-summary-card">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className={`apme-status-icon ${hasFailed ? "failed" : "passed"}`}>
            {hasFailed ? "\u2717" : "\u2713"}
          </div>
          <span style={{ fontSize: 20, fontWeight: 600, color: hasFailed ? "var(--apme-red)" : "var(--apme-green)" }}>
            {hasFailed ? "FAILED" : "PASSED"}
          </span>
        </div>
        <div className="apme-summary-counts">
          {secrets > 0 && (
            <div className="apme-count-box">
              <div className="apme-count-box-value" style={{ color: "var(--apme-red)", fontWeight: 700 }}>{secrets}</div>
              <div className="apme-count-box-label">Secrets</div>
            </div>
          )}
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-red)" }}>{errors}</div>
            <div className="apme-count-box-label">Errors</div>
          </div>
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-yellow)" }}>{warnings}</div>
            <div className="apme-count-box-label">Warnings</div>
          </div>
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-purple)" }}>{infos}</div>
            <div className="apme-count-box-label">Info</div>
          </div>
          <div className="apme-count-box">
            <div className="apme-count-box-value" style={{ color: "var(--apme-orange)" }}>{hints}</div>
            <div className="apme-count-box-label">Hints</div>
          </div>
          {scan.summary && scan.summary.auto_fixable > 0 && (
            <div className="apme-count-box">
              <div className="apme-count-box-value" style={{ color: "var(--apme-green)" }}>{scan.summary.auto_fixable}</div>
              <div className="apme-count-box-label">Fixable</div>
            </div>
          )}
        </div>
      </div>

      <div className="apme-violations-section">
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--apme-border)", fontSize: 16, fontWeight: 600 }}>
          Violations by File
        </div>
        {groups.size === 0 ? (
          <div className="apme-empty">No violations found.</div>
        ) : (
          Array.from(groups.entries()).map(([file, violations]) => (
            <div className="apme-file-group" key={file}>
              <div className="apme-file-header" onClick={() => toggleFile(file)}>
                <span style={{ color: "var(--apme-text-dimmed)" }}>{expanded.has(file) ? "\u25BC" : "\u25B6"}</span>
                <span className="apme-file-name">{file}</span>
                <span className="apme-file-count">{violations.length} issues</span>
              </div>
              {expanded.has(file) &&
                violations.map((v) => (
                  <div className="apme-violation-item" key={v.id}>
                    <span className={`apme-severity ${severityClass(v.level, v.rule_id)}`}>
                      {severityLabel(v.level, v.rule_id)}
                    </span>
                    <span className="apme-rule-id">{v.rule_id}</span>
                    <span className="apme-line-number">
                      {v.line != null ? `:${v.line}` : ""}
                    </span>
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
