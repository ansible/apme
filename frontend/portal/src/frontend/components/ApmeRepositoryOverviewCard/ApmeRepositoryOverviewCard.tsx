/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import { useMemo } from "react";
import type { Entity } from "@backstage/catalog-model";
import { useSearchParams } from "react-router-dom";
import { makeStyles, useTheme } from "@material-ui/core";
import { useApmeAiEnabled } from "../../hooks/useApmeEnabled";
import { useApmeProjectContext } from "../../hooks/useApmeProjectContext";
import { ApmeUnavailable } from "../ApmeUnavailable";
import {
  APME_GATEWAY_UNAVAILABLE_MESSAGE,
  isApmeConnectionError,
} from "../../utils/apmeConnectionError";

import {
  categoryBreakdown,
  fixableViolationCount,
  severityBreakdown,
} from "../../utils/violationAnalytics";
import { useApmeColorTokens } from "../../hooks/useApmeColorTokens";
import {
  ApmeRepositoryOverviewClean,
  ApmeRepositoryOverviewFindingsUnavailable,
  ApmeRepositoryOverviewLoading,
  ApmeRepositoryOverviewNoScan,
  ApmeRepositoryOverviewViolations,
  type CategoryMeta,
} from "./ApmeRepositoryOverviewCardStates";

const CATEGORY_META: CategoryMeta[] = [
  {
    key: "modernize",
    label: "Compatibility",
    tip: "Deprecated syntax, removed modules, and version-specific issues that block AAP upgrades",
  },
  {
    key: "secrets",
    label: "Secrets",
    tip: "Hardcoded credentials, tokens, and keys that should use vault or an external secrets manager",
  },
  {
    key: "risk",
    label: "Risk",
    tip: "Security risks such as missing file permissions, unsafe shell usage, and privilege escalation",
  },
  {
    key: "lint",
    label: "Lint",
    tip: "Code quality issues — naming conventions, FQCN usage, Jinja spacing, and changed_when",
  },
  {
    key: "policy",
    label: "Policy",
    tip: "Organizational policy violations defined by OPA rules",
  },
  {
    key: "dependencies",
    label: "Dependencies",
    tip: "Outdated or vulnerable collection and Python dependencies",
  },
];

const useStyles = makeStyles((theme) => ({
  card: {
    borderRadius: 8,
    // Parent detailsRightColumn already provides gap — no extra marginBottom.
  },
  cardClickable: {
    cursor: "pointer",
  },
  categoryRow: {
    display: "flex",
    alignItems: "center",
    gap: theme.spacing(1),
    marginBottom: 6,
  },
  categoryHelp: {
    display: "inline-flex",
    cursor: "help",
  },
  track: {
    flex: 1,
    minWidth: 0,
    height: 6,
    borderRadius: 3,
    overflow: "hidden",
    backgroundColor:
      theme.palette.type === "dark"
        ? "rgba(255,255,255,0.08)"
        : "rgba(0,0,0,0.06)",
  },
}));

export interface ApmeRepositoryOverviewCardProps {
  context: { entity: Entity };
  /** NFS adapters supply host-owned navigation; legacy callers retain URL navigation. */
  onOpenQuality?: () => void;
  onOpenLatestScan?: () => void;
  navigationDisabled?: boolean;
}

function hasNoQualityScans(
  project: ReturnType<typeof useApmeProjectContext>["project"],
): boolean {
  return (
    !project ||
    (project.scan_count === 0 &&
      !project.latest_scan &&
      !project.last_scanned_at)
  );
}

function findingsUnavailable(
  ctx: ReturnType<typeof useApmeProjectContext>,
  totalViolations: number,
  reportedViolations: number,
): boolean {
  return (
    Boolean(ctx.error) ||
    Boolean(ctx.violationsError) ||
    Boolean(ctx.project && totalViolations === 0 && reportedViolations > 0)
  );
}

export const ApmeRepositoryOverviewCard = ({
  context,
  onOpenQuality,
  onOpenLatestScan,
  navigationDisabled = false,
}: ApmeRepositoryOverviewCardProps) => {
  const classes = useStyles();
  const theme = useTheme();
  const colorTokens = useApmeColorTokens();
  const enableAi = useApmeAiEnabled();
  const [searchParams, setSearchParams] = useSearchParams();
  const ctx = useApmeProjectContext(context.entity);

  const navigateToQualityTab = () => {
    if (navigationDisabled) return;
    if (onOpenQuality) {
      onOpenQuality();
      return;
    }
    const params = new URLSearchParams(searchParams);
    params.set("tab", "quality");
    params.delete("category");
    params.delete("rule");
    params.delete("activity");
    setSearchParams(params, { replace: true });
  };

  const navigateToLastScan = () => {
    if (navigationDisabled) return;
    if (onOpenLatestScan) {
      onOpenLatestScan();
      return;
    }
    const params = new URLSearchParams(searchParams);
    params.set("tab", "quality-activity");
    params.delete("category");
    params.delete("rule");
    const scanId = ctx.project?.latest_scan?.scan_id;
    if (scanId) {
      params.set("activity", scanId);
    } else {
      params.delete("activity");
    }
    setSearchParams(params, { replace: true });
  };

  const totalViolations = ctx.violations.length;
  const sevCounts = useMemo(
    () => severityBreakdown(ctx.violations),
    [ctx.violations],
  );
  const catCounts = useMemo(
    () => categoryBreakdown(ctx.violations, ctx.rulesById),
    [ctx.violations, ctx.rulesById],
  );
  const fixable = useMemo(
    () => fixableViolationCount(ctx.violations, enableAi),
    [ctx.violations, enableAi],
  );

  if (ctx.loading || ctx.violationsLoading) {
    return <ApmeRepositoryOverviewLoading />;
  }

  if (ctx.error && isApmeConnectionError(ctx.error.message)) {
    return <ApmeUnavailable message={APME_GATEWAY_UNAVAILABLE_MESSAGE} />;
  }

  const reportedViolations =
    ctx.project?.latest_scan?.total_violations ??
    ctx.project?.total_violations ??
    totalViolations;

  if (findingsUnavailable(ctx, totalViolations, reportedViolations)) {
    return (
      <ApmeRepositoryOverviewFindingsUnavailable
        classes={classes}
        navigationDisabled={navigationDisabled}
        onViewDetails={navigateToQualityTab}
      />
    );
  }

  if (hasNoQualityScans(ctx.project)) {
    return (
      <ApmeRepositoryOverviewNoScan
        classes={classes}
        navigationDisabled={navigationDisabled}
        repoUrl={ctx.repoUrl}
        onOpenQuality={onOpenQuality}
        onScan={navigateToQualityTab}
      />
    );
  }

  if (totalViolations === 0) {
    return (
      <ApmeRepositoryOverviewClean
        classes={classes}
        navigationDisabled={navigationDisabled}
        colorTokens={colorTokens}
        onOpenLatestScan={navigateToLastScan}
      />
    );
  }

  return (
    <ApmeRepositoryOverviewViolations
      classes={classes}
      theme={theme}
      colorTokens={colorTokens}
      navigationDisabled={navigationDisabled}
      categoryMeta={CATEGORY_META}
      totalViolations={totalViolations}
      reportedViolations={reportedViolations}
      fixable={fixable}
      sevCounts={sevCounts}
      catCounts={catCounts}
      violations={ctx.violations}
      rulesById={ctx.rulesById}
      onOpenLatestScan={navigateToLastScan}
    />
  );
};
