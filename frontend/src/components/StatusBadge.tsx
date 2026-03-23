interface StatusBadgeProps {
  violations: number;
  scanType: string;
}

export function StatusBadge({ violations, scanType }: StatusBadgeProps) {
  if (scanType === "fix" && violations === 0) {
    return <span className="apme-badge passed">{"\u2713"} FIXED</span>;
  }
  if (violations > 0) {
    return <span className="apme-badge failed">{"\u2717"} ISSUES</span>;
  }
  return <span className="apme-badge passed">{"\u2713"} CLEAN</span>;
}
