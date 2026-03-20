interface StatusBadgeProps {
  status: string;
  violations: number;
}

export function StatusBadge({ status, violations }: StatusBadgeProps) {
  if (status === "running") {
    return <span className="apme-badge running">&#9679; RUNNING</span>;
  }
  if (status === "failed") {
    return <span className="apme-badge failed">&#10007; ERROR</span>;
  }
  if (violations > 0) {
    return <span className="apme-badge failed">&#10007; FAILED</span>;
  }
  return <span className="apme-badge passed">&#10003; PASSED</span>;
}
