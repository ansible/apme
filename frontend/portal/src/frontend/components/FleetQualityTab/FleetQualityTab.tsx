/*
 * Copyright Red Hat
 *
 * Fleet quality overview — aligned with ansible-portal-prototypes
 * `design/apme-integration` → QualityOverviewContent.tsx
 */

import { useMemo, useState, type MouseEvent } from "react";
import {
  Box,
  Card,
  Chip,
  Collapse,
  IconButton,
  Link,
  Tooltip,
  Typography,
  makeStyles,
  useTheme,
} from "@material-ui/core";
import CheckCircleIcon from "@material-ui/icons/CheckCircle";
import ChevronRightIcon from "@material-ui/icons/ChevronRight";
import KeyboardArrowDownIcon from "@material-ui/icons/KeyboardArrowDown";
import { LinkButton, Progress } from "@backstage/core-components";
import AddIcon from "@material-ui/icons/Add";
import {
  SEVERITY_ORDER,
  normalizeSeverity,
  categoryLabel,
  effectiveFixType,
  fixTierShortLabel,
  getFixTypeColorTokens,
  type SeverityLevel,
} from "../../../common/severity";
import { useApmeColorTokens } from "../../hooks/useApmeColorTokens";
import { useApmeEnabled, useApmeAiEnabled } from "../../hooks/useApmeEnabled";
import { PreviewLabelRow } from "../PreviewChip";
import { ApmeUnavailable } from "../ApmeUnavailable";
import { APME_GATEWAY_UNAVAILABLE_MESSAGE } from "../../utils/apmeConnectionError";
import { APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH } from "../ApmeAddRepositoryHeaderAction/ApmeAddRepositoryHeaderAction";
import { FleetQualityNoScansEmptyState } from "./FleetQualityNoScansEmptyState";
import { useFleetQualityData } from "./useFleetQualityData";
import { formatFleetViolationSummary } from "./fleetViolationSummary";

const SEVERITY_WEIGHT: Record<SeverityLevel, number> = {
  critical: 50,
  error: 35,
  high: 20,
  medium: 5,
  low: 2,
  info: 1,
};

function fixTierColor(
  remClass: number,
  enableAi: boolean,
  mode: "light" | "dark",
): string {
  const fixType = effectiveFixType(remClass, enableAi) ?? "manual";
  return getFixTypeColorTokens(mode)[fixType].inlineText;
}

const useStyles = makeStyles((theme) => ({
  summaryBar: {
    display: "flex",
    alignItems: "baseline",
    gap: 8,
    marginBottom: theme.spacing(1),
  },
  sevBar: {
    display: "flex",
    gap: 12,
    marginBottom: theme.spacing(2),
    flexWrap: "wrap",
  },
  sevItem: {
    display: "flex",
    alignItems: "center",
    gap: 4,
    padding: "4px 10px",
    borderRadius: 6,
    cursor: "pointer",
    transition: "all 0.15s",
    border: "1px solid transparent",
  },
  sevItemActive: {
    border: "1px solid",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 13,
    "& thead": {
      backgroundColor:
        theme.palette.type === "dark" ? "rgba(255,255,255,0.04)" : "#f5f5f5",
      borderBottom: `1px solid ${theme.palette.divider}`,
    },
    "& th": {
      textAlign: "left",
      padding: "10px 12px",
      fontWeight: 600,
      fontSize: 12,
      color: theme.palette.text.secondary,
      textTransform: "uppercase",
      letterSpacing: 0.3,
      cursor: "pointer",
      userSelect: "none",
      "&:hover": { color: theme.palette.text.primary },
    },
    "& td": {
      padding: "10px 12px",
      borderBottom: `1px solid ${theme.palette.divider}`,
      verticalAlign: "middle",
    },
    "& tbody tr:last-child td": {
      borderBottom: "none",
    },
    "& tbody tr:hover": {
      backgroundColor: theme.palette.action.hover,
    },
  },
  titleRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: theme.spacing(1),
    marginBottom: theme.spacing(1),
  },
  studyNote: {
    marginTop: theme.spacing(2),
    fontStyle: "italic",
    color: theme.palette.text.secondary,
  },
}));

type SortColumn = "impact" | "severity" | "repos" | "occurrences" | "category";

export interface FleetQualityTabProps {
  repositoryDetailPath: (entityName: string, ruleId?: string) => string;
}

export const FleetQualityTab = ({
  repositoryDetailPath,
}: FleetQualityTabProps) => {
  const classes = useStyles();
  const theme = useTheme();
  const colorTokens = useApmeColorTokens();
  const isDark = theme.palette.type === "dark";
  const enabled = useApmeEnabled();
  const enableAi = useApmeAiEnabled();
  const { value, loading } = useFleetQualityData(enabled);

  const [severityFilters, setSeverityFilters] = useState<Set<SeverityLevel>>(
    new Set(),
  );
  const [categoryFilters, setCategoryFilters] = useState<Set<string>>(
    new Set(),
  );
  const [sortCol, setSortCol] = useState<SortColumn>("impact");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedRule, setExpandedRule] = useState<string | null>(null);

  const filteredGroups = useMemo(() => {
    let result = value?.groups ?? [];
    if (severityFilters.size > 0) {
      result = result.filter((g) =>
        severityFilters.has(normalizeSeverity(g.level)),
      );
    }
    if (categoryFilters.size > 0) {
      result = result.filter(
        (g) => g.category && categoryFilters.has(g.category),
      );
    }
    return result;
  }, [value?.groups, severityFilters, categoryFilters]);

  const sortedGroups = useMemo(() => {
    const sevOrder = SEVERITY_ORDER.reduce(
      (acc, sev, index) => {
        acc[sev] = index;
        return acc;
      },
      {} as Record<SeverityLevel, number>,
    );

    return [...filteredGroups].sort((a, b) => {
      let cmp = 0;
      const sevA = normalizeSeverity(a.level);
      const sevB = normalizeSeverity(b.level);
      switch (sortCol) {
        case "impact":
          cmp =
            SEVERITY_WEIGHT[sevB] * b.repos.length -
            SEVERITY_WEIGHT[sevA] * a.repos.length;
          break;
        case "severity":
          cmp = sevOrder[sevA] - sevOrder[sevB];
          break;
        case "repos":
          cmp = b.repos.length - a.repos.length;
          break;
        case "occurrences":
          cmp = b.totalCount - a.totalCount;
          break;
        case "category":
          cmp = (a.category ?? "").localeCompare(b.category ?? "");
          break;
        default:
          // cmp already initialized to 0
          break;
      }
      return sortAsc ? -cmp : cmp;
    });
  }, [filteredGroups, sortCol, sortAsc]);

  const handleSort = (col: SortColumn) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(false);
    }
  };

  const toggleSeverity = (sev: SeverityLevel) => {
    setSeverityFilters((prev) => {
      const next = new Set(prev);
      if (next.has(sev)) {
        next.delete(sev);
      } else {
        next.add(sev);
      }
      return next;
    });
  };

  const toggleCategory = (cat: string) => {
    setCategoryFilters((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  };

  if (!enabled) {
    return (
      <Typography variant="body2" color="textSecondary">
        Content quality scanning is disabled. Enable ansible.apme.enabled in
        configuration.
      </Typography>
    );
  }

  if (loading) {
    return <Progress />;
  }

  const violationTotal = value?.violationTotal ?? 0;
  const reposWithIssues = value?.reposWithIssues ?? 0;
  const totalRepos = value?.totalRepos ?? 0;
  const hasAnyScan = value?.hasAnyScan ?? false;
  const gatewayUnavailable = value?.gatewayUnavailable ?? false;
  const showFleetContent = totalRepos > 0 && hasAnyScan && !gatewayUnavailable;
  const reposClean = Math.max(0, totalRepos - reposWithIssues);
  const severityCounts = value?.severityCounts ?? {
    critical: 0,
    error: 0,
    high: 0,
    medium: 0,
    low: 0,
    info: 0,
  };
  const allRules = value?.groups ?? [];
  const hasFilter = severityFilters.size > 0 || categoryFilters.size > 0;
  const showNoScansEmptyState =
    totalRepos > 0 && !hasAnyScan && !gatewayUnavailable && !hasFilter;
  const filteredViolationCount = sortedGroups.reduce(
    (s, r) => s + r.totalCount,
    0,
  );
  const fleetViolationSummary = formatFleetViolationSummary({
    hasFilter,
    violationTotal,
    filteredRuleCount: sortedGroups.length,
    allRulesCount: allRules.length,
    reposWithIssues,
  });
  const sevOrder = SEVERITY_ORDER;
  const worstFleetSeverity =
    sevOrder.find((sev) => severityCounts[sev] > 0) ??
    ("medium" as SeverityLevel);
  const headlineColor =
    violationTotal > 0
      ? colorTokens.severity[worstFleetSeverity].inlineText
      : theme.palette.text.primary;
  const sortArrow = (col: SortColumn): string => {
    if (sortCol !== col) return "";
    return sortAsc ? " ↑" : " ↓";
  };

  const renderEmptyState = () => {
    if (hasFilter) {
      return (
        <Typography
          style={{ fontSize: 14, color: theme.palette.text.secondary }}
        >
          No violations match the current filters.
        </Typography>
      );
    }

    if (totalRepos === 0) {
      return (
        <>
          <Typography variant="h6" style={{ fontWeight: 600, marginBottom: 8 }}>
            No Git Repositories Found
          </Typography>
          <Typography
            style={{
              fontSize: 14,
              color: theme.palette.text.secondary,
              marginBottom: 16,
              maxWidth: 480,
              marginLeft: "auto",
              marginRight: "auto",
            }}
          >
            No git repositories were retrieved from the configured sources. Sync
            or add manually to discover latest contents.
          </Typography>
          <LinkButton
            variant="contained"
            color="primary"
            to={APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH}
            startIcon={<AddIcon />}
          >
            Add repository
          </LinkButton>
        </>
      );
    }

    return (
      <>
        <CheckCircleIcon
          style={{
            fontSize: 40,
            color: colorTokens.dependencyViolation.okCheckColor,
            marginBottom: 8,
          }}
        />
        <Typography style={{ fontSize: 16, fontWeight: 500 }}>
          All repositories are clean
        </Typography>
      </>
    );
  };

  return (
    <Box>
      <Box marginBottom={1}>
        <PreviewLabelRow />
      </Box>

      {showFleetContent && (
        <Box className={classes.titleRow}>
          <Typography variant="h6">Fleet quality</Typography>
        </Box>
      )}

      {showFleetContent && (
        <Box className={classes.summaryBar}>
          <Typography
            style={{ fontSize: 20, fontWeight: 700, color: headlineColor }}
          >
            {hasFilter ? filteredViolationCount : violationTotal}
          </Typography>
          <Typography
            style={{ fontSize: 13, color: theme.palette.text.secondary }}
          >
            {fleetViolationSummary}
            {reposClean > 0 && !hasFilter && (
              <span style={{ marginLeft: 6 }}>
                ·{" "}
                <span
                  style={{
                    color: colorTokens.dependencyViolation.okCheckColor,
                    fontWeight: 500,
                  }}
                >
                  {reposClean} clean
                </span>
              </span>
            )}
          </Typography>
        </Box>
      )}

      {showFleetContent && (
        <>
          <Box className={classes.sevBar}>
            {sevOrder.map((sev) => {
              const count = severityCounts[sev];
              if (count === 0) {
                return null;
              }
              const isActive = severityFilters.has(sev);
              const isDimmed =
                hasFilter && !isActive && severityFilters.size > 0;
              const tokens = colorTokens.severity[sev];
              const color = tokens.barFill;
              const inlineColor = tokens.inlineText;
              return (
                <Box
                  key={sev}
                  className={`${classes.sevItem} ${
                    isActive ? classes.sevItemActive : ""
                  }`}
                  style={{
                    backgroundColor: isActive ? `${color}12` : undefined,
                    borderColor: isActive ? `${color}60` : undefined,
                    opacity: isDimmed ? 0.45 : 1,
                  }}
                  onClick={() => toggleSeverity(sev)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      toggleSeverity(sev);
                    }
                  }}
                >
                  <Box
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      backgroundColor: color,
                    }}
                  />
                  <Typography
                    style={{
                      fontSize: 12,
                      textTransform: "capitalize",
                      color: isActive
                        ? inlineColor
                        : theme.palette.text.secondary,
                      fontWeight: isActive ? 600 : 400,
                    }}
                  >
                    {sev}
                  </Typography>
                  <Typography style={{ fontSize: 12, fontWeight: 700, color }}>
                    {count}
                  </Typography>
                </Box>
              );
            })}
          </Box>

          {hasFilter && (
            <Box
              display="flex"
              alignItems="center"
              style={{ marginBottom: 12, gap: 8 }}
            >
              <Typography
                style={{ fontSize: 12, color: theme.palette.text.secondary }}
              >
                Showing {filteredViolationCount} of {violationTotal} violations
              </Typography>
              {Array.from(categoryFilters).map((cat) => (
                <Chip
                  key={cat}
                  size="small"
                  label={categoryLabel(cat)}
                  onDelete={() => toggleCategory(cat)}
                  style={{ height: 20, fontSize: 11, fontWeight: 600 }}
                />
              ))}
              <button
                type="button"
                onClick={() => {
                  setSeverityFilters(new Set());
                  setCategoryFilters(new Set());
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    setSeverityFilters(new Set());
                    setCategoryFilters(new Set());
                  }
                }}
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  fontSize: 12,
                  color: theme.palette.primary.main,
                  cursor: "pointer",
                }}
              >
                Clear filters
              </button>
            </Box>
          )}

          <Card
            variant="outlined"
            style={{ borderRadius: 8, overflow: "hidden" }}
          >
            <Box style={{ overflow: "auto" }}>
              <table className={classes.table}>
                <thead>
                  <tr>
                    <th style={{ width: 36, padding: "10px 4px" }} />
                    <th
                      style={{ width: 100 }}
                      onClick={() => handleSort("severity")}
                    >
                      Severity{sortArrow("severity")}
                    </th>
                    <th onClick={() => handleSort("impact")}>
                      Rule{sortArrow("impact")}
                    </th>
                    <th
                      style={{ width: 110 }}
                      onClick={() => handleSort("category")}
                    >
                      Category{sortArrow("category")}
                    </th>
                    <th
                      style={{ width: 72 }}
                      onClick={() => handleSort("repos")}
                    >
                      Repos{sortArrow("repos")}
                    </th>
                    <th
                      style={{ width: 100 }}
                      onClick={() => handleSort("occurrences")}
                    >
                      Occurrences{sortArrow("occurrences")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedGroups.map((group) => {
                    const sev = normalizeSeverity(group.level);
                    const sevTokens = colorTokens.severity[sev];
                    const isExpanded = expandedRule === group.ruleId;

                    return [
                      <tr
                        key={group.ruleId}
                        onClick={() =>
                          setExpandedRule(isExpanded ? null : group.ruleId)
                        }
                        style={{
                          cursor: "pointer",
                          ...(isExpanded
                            ? {
                                backgroundColor: isDark
                                  ? "rgba(255,255,255,0.03)"
                                  : "rgba(0,0,0,0.015)",
                              }
                            : {}),
                        }}
                      >
                        <td style={{ width: 36, padding: "8px 4px" }}>
                          <IconButton size="small">
                            {isExpanded ? (
                              <KeyboardArrowDownIcon />
                            ) : (
                              <ChevronRightIcon />
                            )}
                          </IconButton>
                        </td>
                        <td>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "2px 8px",
                              borderRadius: 3,
                              fontSize: 11,
                              fontWeight: 600,
                              textTransform: "uppercase",
                              letterSpacing: 0.3,
                              whiteSpace: "nowrap",
                              backgroundColor: sevTokens.pillBackground,
                              color: sevTokens.pillText,
                            }}
                          >
                            {group.totalCount} {sev}
                          </span>
                        </td>
                        <td>
                          <Tooltip
                            title={`Rule ID: ${group.ruleId}`}
                            arrow
                            enterDelay={400}
                          >
                            <Typography style={{ fontSize: 13 }}>
                              {group.message}
                            </Typography>
                          </Tooltip>
                        </td>
                        <td>
                          {group.category ? (
                            <Typography
                              onClick={(e: MouseEvent) => {
                                e.stopPropagation();
                                toggleCategory(group.category!);
                              }}
                              style={{
                                fontSize: 11,
                                color: categoryFilters.has(group.category)
                                  ? theme.palette.primary.main
                                  : theme.palette.text.secondary,
                                cursor: "pointer",
                                fontWeight: categoryFilters.has(group.category)
                                  ? 600
                                  : 400,
                              }}
                            >
                              {categoryLabel(group.category)}
                            </Typography>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>
                          <Typography style={{ fontSize: 13, fontWeight: 500 }}>
                            {group.repos.length}
                          </Typography>
                        </td>
                        <td>
                          <Typography style={{ fontSize: 13, fontWeight: 500 }}>
                            {group.totalCount}
                          </Typography>
                        </td>
                      </tr>,
                      isExpanded ? (
                        <tr key={`${group.ruleId}-repos`}>
                          <td
                            colSpan={6}
                            style={{
                              padding: 0,
                              backgroundColor: isDark
                                ? "rgba(255,255,255,0.02)"
                                : "#fafafa",
                            }}
                          >
                            <Collapse in={isExpanded}>
                              <Box style={{ padding: "4px 0 4px 52px" }}>
                                {group.repos.map((r) => (
                                  <Box
                                    key={r.project.id}
                                    display="flex"
                                    alignItems="center"
                                    style={{
                                      padding: "6px 12px",
                                      borderBottom: `1px solid ${theme.palette.divider}`,
                                      gap: 12,
                                    }}
                                  >
                                    <Box
                                      display="flex"
                                      alignItems="center"
                                      style={{ gap: 6, flex: 1, minWidth: 0 }}
                                    >
                                      <Typography
                                        style={{
                                          fontSize: 12,
                                          fontWeight: 500,
                                        }}
                                      >
                                        {r.project.name}
                                      </Typography>
                                      {r.count > 1 && (
                                        <Chip
                                          size="small"
                                          label={`×${r.count}`}
                                          style={{
                                            fontSize: 10,
                                            height: 16,
                                            fontWeight: 600,
                                          }}
                                        />
                                      )}
                                    </Box>
                                    <Typography
                                      style={{
                                        fontSize: 11,
                                        fontWeight: 500,
                                        color: fixTierColor(
                                          r.remediationClass,
                                          enableAi,
                                          colorTokens.mode,
                                        ),
                                        flexShrink: 0,
                                      }}
                                    >
                                      {fixTierShortLabel(
                                        r.remediationClass,
                                        enableAi,
                                      )}
                                    </Typography>
                                    <Typography
                                      style={{
                                        fontSize: 11,
                                        color: theme.palette.text.secondary,
                                        flexShrink: 0,
                                      }}
                                    >
                                      {r.lastScannedAt ?? "—"}
                                    </Typography>
                                    <Link
                                      href={repositoryDetailPath(
                                        r.entityName,
                                        group.ruleId,
                                      )}
                                      style={{
                                        fontSize: 11,
                                        fontWeight: 500,
                                        flexShrink: 0,
                                      }}
                                      onClick={(e: MouseEvent) =>
                                        e.stopPropagation()
                                      }
                                    >
                                      View details →
                                    </Link>
                                  </Box>
                                ))}
                              </Box>
                            </Collapse>
                          </td>
                        </tr>
                      ) : null,
                    ];
                  })}
                </tbody>
              </table>
            </Box>
          </Card>
        </>
      )}

      {gatewayUnavailable && totalRepos > 0 && (
        <ApmeUnavailable message={APME_GATEWAY_UNAVAILABLE_MESSAGE} />
      )}

      {showNoScansEmptyState && <FleetQualityNoScansEmptyState />}

      {sortedGroups.length === 0 &&
        !showNoScansEmptyState &&
        !(gatewayUnavailable && totalRepos > 0) && (
          <Box style={{ textAlign: "center", padding: "48px 24px" }}>
            {renderEmptyState()}
          </Box>
        )}

      {/* Fleet Quality view */}
    </Box>
  );
};
