export function formatFleetViolationSummary(options: {
  hasFilter: boolean;
  violationTotal: number;
  filteredRuleCount: number;
  allRulesCount: number;
  reposWithIssues: number;
}): string {
  const {
    hasFilter,
    violationTotal,
    filteredRuleCount,
    allRulesCount,
    reposWithIssues,
  } = options;
  if (!hasFilter) {
    return `violations · ${allRulesCount} rules · ${reposWithIssues} repositories`;
  }
  const rulesWord = filteredRuleCount === 1 ? "rule" : "rules";
  return `of ${violationTotal} violations · ${filteredRuleCount} ${rulesWord} · ${reposWithIssues} repositories`;
}
