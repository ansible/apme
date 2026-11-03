/*
 * Copyright Red Hat
 *
 * Repo detail Quality activity — SPA project Activity list/detail (US-007).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { configApiRef, useApi } from "@backstage/core-plugin-api";
import { useEntity } from "@backstage/plugin-catalog-react";
import { Progress, ResponseErrorPanel } from "@backstage/core-components";
import { makeStyles, useTheme } from "@material-ui/core";
import { Button, Card, CardBody, Flex, FlexItem } from "@patternfly/react-core";
import { ExternalLinkAltIcon } from "@patternfly/react-icons";
import "@patternfly/react-core/dist/styles/base.css";
import { ApmeApiProvider, toPrFilesDiffUrl } from "@apme/ui-workflow";
import type { Activity, ActivityDetail } from "../../../common/types";
import {
  defaultBranchFromEntity,
  normalizeRepoUrlFromEntity,
} from "@ansible/backstage-rhaap-common/catalogEntity";
import { apmeApiRef } from "../../api";
import { useResolveApmeProject } from "../../hooks/useResolveApmeProject";
import { useSyncPatternFlyTheme } from "../../hooks/useSyncPatternFlyTheme";
import { resolveDefaultBranchDevSpacesUrl } from "../../utils/resolveDefaultBranchDevSpacesUrl";
import {
  ApmeOutlinedTableCard,
  useApmeOutlinedTableStyles,
} from "../ApmeOutlinedTable";
import { ApmeUnavailable } from "../ApmeUnavailable";
import { ActivityPublishedCell } from "../ActivityPublishedCell";
import { EditInDevSpacesButton } from "../EditInDevSpacesButton";
import { PreviewLabelRow } from "../PreviewChip";
import { QualityFindingsSection } from "../QualityFindingsSection";

function scanTypeChipStyle(isFix: boolean, isDark: boolean) {
  if (isFix) {
    return {
      backgroundColor: isDark
        ? "rgba(62, 134, 53, 0.25)"
        : "rgba(62, 134, 53, 0.12)",
      color: isDark ? "#BDE5B8" : "#3E8635",
    };
  }
  return {
    backgroundColor: isDark
      ? "rgba(0, 102, 204, 0.25)"
      : "rgba(0, 102, 204, 0.1)",
    color: isDark ? "#8EC8F7" : "#0066CC",
  };
}

const useActivityListStyles = makeStyles({
  typeChip: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.4,
  },
});

type ActivitySortColumn =
  "type" | "violations" | "fixable" | "remediated" | "manual" | "time";

/** In-page panel dismiss — labeled (not icon-only); keeps control on the right. */
function CloseDetailButton({ onClose }: Readonly<{ onClose: () => void }>) {
  return (
    <Button
      variant="link"
      isInline
      aria-label="Close activity detail"
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      Close
    </Button>
  );
}

function displayType(scanType: string): string {
  if (scanType === "scan") return "check";
  if (scanType === "fix") return "remediate";
  return scanType;
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return iso;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function ActivityList({
  activities,
  onOpen,
}: Readonly<{
  activities: Activity[];
  onOpen: (scanId: string) => void;
}>) {
  const classes = useActivityListStyles();
  const tableClasses = useApmeOutlinedTableStyles();
  const theme = useTheme();
  const isDark = theme.palette.type === "dark";
  const [sortCol, setSortCol] = useState<ActivitySortColumn>("time");
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    return [...activities].sort((a, b) => {
      let cmp = 0;
      switch (sortCol) {
        case "type":
          cmp = displayType(a.scan_type).localeCompare(
            displayType(b.scan_type),
          );
          break;
        case "violations":
          cmp = a.total_violations - b.total_violations;
          break;
        case "fixable":
          cmp = a.fixable - b.fixable;
          break;
        case "remediated":
          cmp = a.remediated_count - b.remediated_count;
          break;
        case "manual":
          cmp = a.manual_review - b.manual_review;
          break;
        case "time":
        default:
          cmp =
            new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [activities, sortCol, sortAsc]);

  const handleSort = (col: ActivitySortColumn) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(col === "type");
    }
  };

  const sortArrow = (col: ActivitySortColumn): string => {
    if (sortCol !== col) return "";
    return sortAsc ? " ↑" : " ↓";
  };

  if (activities.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: "center", opacity: 0.6 }}>
        No quality activity recorded yet. Run a scan from the Quality tab.
      </div>
    );
  }

  return (
    <ApmeOutlinedTableCard>
      <table className={tableClasses.table} role="grid">
        <thead>
          <tr>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 110 }}
              onClick={() => handleSort("type")}
            >
              Type{sortArrow("type")}
            </th>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 100 }}
              onClick={() => handleSort("violations")}
            >
              Violations{sortArrow("violations")}
            </th>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 88 }}
              onClick={() => handleSort("fixable")}
            >
              Fixable{sortArrow("fixable")}
            </th>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 110 }}
              onClick={() => handleSort("remediated")}
            >
              Remediated{sortArrow("remediated")}
            </th>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 88 }}
              onClick={() => handleSort("manual")}
            >
              Manual{sortArrow("manual")}
            </th>
            <th
              className={tableClasses.sortableHeader}
              style={{ width: 120 }}
              onClick={() => handleSort("time")}
            >
              Time{sortArrow("time")}
            </th>
            <th style={{ width: 140 }}>Published</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((scan) => {
            const isFix = scan.scan_type === "remediate";
            const typeLabel = displayType(scan.scan_type);
            return (
              <tr
                key={scan.scan_id}
                role="row"
                tabIndex={0}
                onClick={() => onOpen(scan.scan_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(scan.scan_id);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <td>
                  <span
                    className={classes.typeChip}
                    style={scanTypeChipStyle(isFix, isDark)}
                  >
                    {typeLabel}
                  </span>
                </td>
                <td>{scan.total_violations}</td>
                <td style={{ opacity: isFix ? 0.35 : 1 }}>
                  {isFix ? "—" : scan.fixable}
                </td>
                <td style={{ opacity: isFix ? 1 : 0.35 }}>
                  {isFix ? scan.remediated_count : "—"}
                </td>
                <td>{scan.manual_review}</td>
                <td style={{ color: theme.palette.text.secondary }}>
                  {timeAgo(scan.created_at)}
                </td>
                <td onClick={(e) => e.stopPropagation()}>
                  <ActivityPublishedCell
                    pr_url={scan.pr_url}
                    branch_name={scan.branch_name}
                    commit_sha={scan.commit_sha}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ApmeOutlinedTableCard>
  );
}

function ActivityDetailView({
  detail,
  onBack,
  ruleFilter,
}: Readonly<{
  detail: ActivityDetail;
  onBack: () => void;
  ruleFilter?: string;
}>) {
  return (
    <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
      <Flex
        justifyContent={{ default: "justifyContentSpaceBetween" }}
        alignItems={{ default: "alignItemsFlexStart" }}
      >
        <FlexItem>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            {displayType(detail.scan_type)}
          </div>
          <div style={{ opacity: 0.7, marginTop: 4 }}>
            {new Date(detail.created_at).toLocaleString()} ·{" "}
            {detail.total_violations} violations
          </div>
          {(detail.pr_url || detail.branch_name) && (
            <Flex
              gap={{ default: "gapSm" }}
              alignItems={{ default: "alignItemsCenter" }}
              style={{ marginTop: 8 }}
            >
              {detail.pr_url && (
                <FlexItem>
                  <Button
                    variant="link"
                    isInline
                    component="a"
                    href={toPrFilesDiffUrl(detail.pr_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    icon={<ExternalLinkAltIcon />}
                    iconPosition="end"
                    size="sm"
                  >
                    View PR
                  </Button>
                </FlexItem>
              )}
              {detail.branch_name && (
                <FlexItem>
                  <span
                    style={{
                      fontFamily: "var(--pf-t--global--font--family--mono)",
                      fontSize: "0.875em",
                    }}
                  >
                    {detail.branch_name}
                    {detail.commit_sha
                      ? ` @ ${detail.commit_sha.slice(0, 8)}`
                      : ""}
                  </span>
                </FlexItem>
              )}
            </Flex>
          )}
        </FlexItem>
        <FlexItem>
          <CloseDetailButton onClose={onBack} />
        </FlexItem>
      </Flex>
      <QualityFindingsSection
        violations={detail.violations}
        ruleFilter={ruleFilter}
        description="Findings from this past quality check (read-only)."
      />
    </Flex>
  );
}

function QualityActivityBody({ projectId }: Readonly<{ projectId: string }>) {
  const apmeApi = useApi(apmeApiRef);
  const configApi = useApi(configApiRef);
  const { entity } = useEntity();
  const [searchParams, setSearchParams] = useSearchParams();
  const activityId = searchParams.get("activity") ?? undefined;
  const ruleFilter = searchParams.get("rule") ?? undefined;

  const devSpacesUrl = resolveDefaultBranchDevSpacesUrl({
    devSpacesBaseUrl: configApi.getOptionalString("ansible.devSpaces.baseUrl"),
    repoUrl: normalizeRepoUrlFromEntity(entity),
    branch: defaultBranchFromEntity(entity),
  });

  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [listError, setListError] = useState<Error | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const openActivity = useCallback(
    (scanId: string, replace = false) => {
      const next = new URLSearchParams(searchParams);
      next.set("activity", scanId);
      setSearchParams(next, { replace });
    },
    [searchParams, setSearchParams],
  );

  const backToList = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("activity");
    setSearchParams(next, { replace: true });
    setDetail(null);
    setDetailError(null);
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await apmeApi.getActivity(projectId);
        if (!cancelled) {
          setActivities(rows);
          setListError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setListError(e instanceof Error ? e : new Error(String(e)));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apmeApi, projectId]);

  // Fleet drill-down (?rule=): open the latest scan automatically.
  useEffect(() => {
    if (activityId || !ruleFilter || !activities?.length) return;
    const latest = [...activities].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )[0];
    if (latest) {
      openActivity(latest.scan_id, true);
    }
  }, [activityId, ruleFilter, activities, openActivity]);

  useEffect(() => {
    if (!activityId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return undefined;
    }
    let cancelled = false;
    setDetailLoading(true);
    void (async () => {
      try {
        const next = await apmeApi.getActivityDetail(activityId);
        if (!cancelled) {
          setDetail(next);
          setDetailError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setDetail(null);
          setDetailError(e instanceof Error ? e : new Error(String(e)));
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apmeApi, activityId]);

  if (listError && !activityId) {
    return <ResponseErrorPanel error={listError} />;
  }
  if (!activities && !activityId) {
    return <Progress />;
  }

  if (activityId) {
    let detailBody: JSX.Element;
    if (detailLoading) {
      detailBody = <Progress />;
    } else if (detailError) {
      detailBody = (
        <Flex direction={{ default: "column" }} gap={{ default: "gapSm" }}>
          <Flex justifyContent={{ default: "justifyContentFlexEnd" }}>
            <CloseDetailButton onClose={backToList} />
          </Flex>
          <ResponseErrorPanel error={detailError} />
        </Flex>
      );
    } else if (detail) {
      detailBody = (
        <ActivityDetailView
          detail={detail}
          onBack={backToList}
          ruleFilter={ruleFilter}
        />
      );
    } else {
      detailBody = (
        <Flex direction={{ default: "column" }} gap={{ default: "gapSm" }}>
          <Flex justifyContent={{ default: "justifyContentFlexEnd" }}>
            <CloseDetailButton onClose={backToList} />
          </Flex>
          <div style={{ opacity: 0.7 }}>Activity not found.</div>
        </Flex>
      );
    }

    return (
      <Card>
        <CardBody>{detailBody}</CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardBody>
        <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
          <PreviewLabelRow />
          <Flex
            justifyContent={{ default: "justifyContentSpaceBetween" }}
            alignItems={{ default: "alignItemsCenter" }}
          >
            <div style={{ fontSize: 18, fontWeight: 600 }}>
              Quality activity ({activities?.length ?? 0})
            </div>
            <EditInDevSpacesButton url={devSpacesUrl} />
          </Flex>
          <ActivityList activities={activities ?? []} onOpen={openActivity} />
        </Flex>
      </CardBody>
    </Card>
  );
}

/**
 * Thin host: list project activity; open past scan via ?activity= with Back.
 */
export const ApmeQualityActivityTab = () => {
  useSyncPatternFlyTheme();
  const { adapter, projectId, error, unavailable } = useResolveApmeProject();

  if (unavailable) {
    return <ApmeUnavailable />;
  }
  if (error) {
    return <ResponseErrorPanel error={error} />;
  }
  if (!adapter || !projectId) {
    return <Progress />;
  }

  return (
    <ApmeApiProvider adapter={adapter}>
      <QualityActivityBody projectId={projectId} />
    </ApmeApiProvider>
  );
};
