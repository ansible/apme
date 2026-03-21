import { useEffect, useMemo, useState } from "react";
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

type SeverityKey = "secrets" | "errors" | "very_high" | "high" | "medium" | "warnings" | "low" | "very_low" | "hints";

const SEVERITY_DEFS: { key: SeverityKey; label: string; cssVar: string; filterValue: string; match: (cls: string) => boolean }[] = [
  { key: "secrets", label: "Secrets", cssVar: "--apme-sev-critical", filterValue: "critical", match: (c) => c === "critical" },
  { key: "errors", label: "Error", cssVar: "--apme-sev-error", filterValue: "error", match: (c) => c === "error" },
  { key: "very_high", label: "V.High", cssVar: "--apme-sev-very-high", filterValue: "very-high", match: (c) => c === "very-high" },
  { key: "high", label: "High", cssVar: "--apme-sev-high", filterValue: "high", match: (c) => c === "high" },
  { key: "medium", label: "Med", cssVar: "--apme-sev-medium", filterValue: "medium", match: (c) => c === "medium" },
  { key: "warnings", label: "Warn", cssVar: "--apme-sev-warning", filterValue: "warning", match: (c) => c === "warning" },
  { key: "low", label: "Low", cssVar: "--apme-sev-low", filterValue: "low", match: (c) => c === "low" },
  { key: "very_low", label: "V.Low", cssVar: "--apme-sev-very-low", filterValue: "very-low", match: (c) => c === "very-low" },
  { key: "hints", label: "Hint", cssVar: "--apme-sev-hint", filterValue: "hint", match: (c) => c === "hint" },
];

function toggleSetValue<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export function ScanDetailPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [scan, setScan] = useState<ScanOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedLevels, setSelectedLevels] = useState<Set<string>>(new Set());
  const [selectedRules, setSelectedRules] = useState<Set<string>>(new Set());
  const [showLevelPicker, setShowLevelPicker] = useState(false);
  const [showRulePicker, setShowRulePicker] = useState(false);

  useEffect(() => {
    if (!scanId) return;
    setLoading(true);
    getScan(scanId)
      .then(setScan)
      .catch(() => setScan(null))
      .finally(() => setLoading(false));
  }, [scanId]);

  const counts = useMemo(() => {
    if (!scan) return {} as Record<SeverityKey, number>;
    const c: Record<SeverityKey, number> = {
      secrets: 0, errors: 0, very_high: 0, high: 0,
      medium: 0, warnings: 0, low: 0, very_low: 0, hints: 0,
    };
    const secretKeys = new Set<string>();
    for (const v of scan.violations) {
      const cls = severityClass(v.level, v.rule_id);
      if (v.rule_id.startsWith("SEC")) {
        secretKeys.add(`${v.file}\0${v.rule_id}\0${v.message}`);
      }
      const def = SEVERITY_DEFS.find((d) => d.match(cls));
      if (def && def.key !== "secrets") c[def.key]++;
    }
    c.secrets = secretKeys.size;
    return c;
  }, [scan]);

  const ruleIds = useMemo(() => {
    if (!scan) return [];
    const ids = new Set(scan.violations.map((v) => v.rule_id));
    return Array.from(ids).sort();
  }, [scan]);

  const filteredViolations = useMemo(() => {
    if (!scan) return [];
    return scan.violations.filter((v) => {
      if (selectedLevels.size > 0) {
        const cls = severityClass(v.level, v.rule_id);
        if (!selectedLevels.has(cls)) return false;
      }
      if (selectedRules.size > 0 && !selectedRules.has(v.rule_id)) return false;
      return true;
    });
  }, [scan, selectedLevels, selectedRules]);

  const groups = useMemo(() => groupByFile(filteredViolations), [filteredViolations]);

  const filesScanned = useMemo(() => {
    if (!scan?.diagnostics || typeof scan.diagnostics !== "object") return null;
    if ("file_count" in scan.diagnostics) return scan.diagnostics.file_count as number;
    if ("files_scanned" in scan.diagnostics) return scan.diagnostics.files_scanned as number;
    return null;
  }, [scan]);

  if (loading) return <div className="apme-empty">Loading...</div>;
  if (!scan) return <div className="apme-empty">Scan not found.</div>;

  const hasFailed = counts.errors > 0 || counts.secrets > 0;
  const source = scan.source || "engine";
  const isFix = source === "fix";
  const activeFilters = selectedLevels.size > 0 || selectedRules.size > 0;

  const expandAll = () => setExpanded(new Set(groups.keys()));
  const collapseAll = () => setExpanded(new Set());
  const clearFilters = () => { setSelectedLevels(new Set()); setSelectedRules(new Set()); };

  const levelSummary = selectedLevels.size === 0
    ? "All Levels"
    : Array.from(selectedLevels).map((l) => SEVERITY_DEFS.find((d) => d.filterValue === l)?.label ?? l).join(", ");

  const ruleSummary = selectedRules.size === 0
    ? "All Rules"
    : Array.from(selectedRules).join(", ");

  const dropdownStyle: React.CSSProperties = {
    position: "absolute",
    top: "100%",
    left: 0,
    zIndex: 100,
    marginTop: 4,
    background: "var(--apme-bg-secondary)",
    border: "1px solid var(--apme-border)",
    borderRadius: 4,
    padding: "6px 0",
    minWidth: 180,
    maxHeight: 280,
    overflowY: "auto",
    boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
  };

  const checkboxItemStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "5px 14px",
    cursor: "pointer",
    fontSize: 13,
    color: "var(--apme-text-primary)",
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
            <span className={`apme-badge ${isFix ? "passed" : "running"}`} style={{ marginRight: 8 }}>
              {isFix ? "fix" : "scan"}
            </span>
            {new Date(scan.created_at).toLocaleString()}
            {scan.diagnostics && typeof scan.diagnostics === "object" && "total_ms" in scan.diagnostics
              ? ` \u2022 Duration: ${(scan.diagnostics.total_ms as number).toFixed(0)}ms`
              : ""}
            {filesScanned != null && ` \u2022 ${filesScanned} files scanned`}
          </p>
        </div>
      </header>

      <div className="apme-summary-card">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className={`apme-status-icon ${hasFailed ? "failed" : "passed"}`}>
            {hasFailed ? "\u2717" : "\u2713"}
          </div>
          <span style={{ fontSize: 20, fontWeight: 600, color: hasFailed ? "var(--apme-sev-critical)" : "var(--apme-green)" }}>
            {hasFailed ? "FAILED" : "PASSED"}
          </span>
        </div>
        <div className="apme-summary-counts">
          {SEVERITY_DEFS.map((def) => {
            const val = counts[def.key];
            if (def.key === "secrets" && val === 0) return null;
            return (
              <div className="apme-count-box" key={def.key}>
                <div
                  className="apme-count-box-value"
                  style={{ color: `var(${def.cssVar})`, fontWeight: def.key === "secrets" ? 700 : undefined }}
                >
                  {val}
                </div>
                <div className="apme-count-box-label">{def.label}</div>
              </div>
            );
          })}
          {isFix && scan.summary && scan.summary.auto_fixable > 0 && (
            <div className="apme-count-box">
              <div className="apme-count-box-value" style={{ color: "var(--apme-green)" }}>{scan.summary.auto_fixable}</div>
              <div className="apme-count-box-label">Fixed</div>
            </div>
          )}
          {!isFix && scan.summary && scan.summary.auto_fixable > 0 && (
            <div className="apme-count-box">
              <div className="apme-count-box-value" style={{ color: "var(--apme-sev-low)" }}>{scan.summary.auto_fixable}</div>
              <div className="apme-count-box-label">Fixable</div>
            </div>
          )}
        </div>
      </div>

      <div className="apme-violations-section">
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--apme-border)", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: 16, fontWeight: 600, marginRight: "auto" }}>
            Violations by File
            {activeFilters && (
              <span style={{ fontSize: 13, fontWeight: 400, color: "var(--apme-text-muted)", marginLeft: 8 }}>
                ({filteredViolations.length} of {scan.violations.length} shown)
              </span>
            )}
          </span>

          {/* Level multi-select */}
          <div style={{ position: "relative" }}>
            <button
              className="apme-btn apme-btn-secondary"
              onClick={() => { setShowLevelPicker((p) => !p); setShowRulePicker(false); }}
              style={{ fontSize: 12, padding: "4px 10px", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {levelSummary}
            </button>
            {showLevelPicker && (
              <div style={dropdownStyle}>
                {SEVERITY_DEFS.map((def) => (
                  <label key={def.filterValue} style={checkboxItemStyle} onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedLevels.has(def.filterValue)}
                      onChange={() => setSelectedLevels((prev) => toggleSetValue(prev, def.filterValue))}
                    />
                    <span className={`apme-severity ${def.filterValue}`} style={{ fontSize: 11 }}>{def.label}</span>
                  </label>
                ))}
                <div style={{ borderTop: "1px solid var(--apme-border)", marginTop: 4, paddingTop: 4, paddingLeft: 14, paddingRight: 14 }}>
                  <button
                    className="apme-btn apme-btn-secondary"
                    onClick={() => { setSelectedLevels(new Set()); setShowLevelPicker(false); }}
                    style={{ fontSize: 11, padding: "2px 8px", width: "100%" }}
                  >
                    Clear
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Rule multi-select */}
          <div style={{ position: "relative" }}>
            <button
              className="apme-btn apme-btn-secondary"
              onClick={() => { setShowRulePicker((p) => !p); setShowLevelPicker(false); }}
              style={{ fontSize: 12, padding: "4px 10px", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {ruleSummary}
            </button>
            {showRulePicker && (
              <div style={dropdownStyle}>
                {ruleIds.map((id) => (
                  <label key={id} style={checkboxItemStyle} onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedRules.has(id)}
                      onChange={() => setSelectedRules((prev) => toggleSetValue(prev, id))}
                    />
                    <span className="apme-rule-id" style={{ fontSize: 12 }}>{id}</span>
                  </label>
                ))}
                <div style={{ borderTop: "1px solid var(--apme-border)", marginTop: 4, paddingTop: 4, paddingLeft: 14, paddingRight: 14 }}>
                  <button
                    className="apme-btn apme-btn-secondary"
                    onClick={() => { setSelectedRules(new Set()); setShowRulePicker(false); }}
                    style={{ fontSize: 11, padding: "2px 8px", width: "100%" }}
                  >
                    Clear
                  </button>
                </div>
              </div>
            )}
          </div>

          {activeFilters && (
            <button
              className="apme-btn apme-btn-secondary"
              onClick={clearFilters}
              style={{ fontSize: 12, padding: "4px 10px" }}
            >
              Reset
            </button>
          )}

          <button className="apme-btn apme-btn-secondary" onClick={expandAll} style={{ fontSize: 12, padding: "4px 10px" }}>
            Expand All
          </button>
          <button className="apme-btn apme-btn-secondary" onClick={collapseAll} style={{ fontSize: 12, padding: "4px 10px" }}>
            Collapse All
          </button>
        </div>

        {/* Close dropdowns when clicking outside */}
        {(showLevelPicker || showRulePicker) && (
          <div
            style={{ position: "fixed", inset: 0, zIndex: 99 }}
            onClick={() => { setShowLevelPicker(false); setShowRulePicker(false); }}
          />
        )}

        {groups.size === 0 ? (
          <div className="apme-empty">{activeFilters ? "No violations match the current filters." : "No violations found."}</div>
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
                  .sort((a, b) => severityOrder(severityClass(a.level, a.rule_id)) - severityOrder(severityClass(b.level, b.rule_id)))
                  .map((v) => (
                  <div className="apme-violation-item" key={v.id}>
                    <span className={`apme-severity ${severityClass(v.level, v.rule_id)}`}>
                      {severityLabel(v.level, v.rule_id)}
                    </span>
                    <span className="apme-rule-id">{v.rule_id}</span>
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
