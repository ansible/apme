/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  IconButton,
  Tooltip,
  Typography,
} from "@material-ui/core";
import HelpOutlineIcon from "@material-ui/icons/HelpOutline";
import RefreshIcon from "@material-ui/icons/Refresh";
import type { Theme } from "@material-ui/core/styles";
import type { SeverityLevel } from "../../../common/severity";
import { SEVERITY_ORDER } from "../../../common/severity";
import type { Violation } from "../../../common/types";
import {
  categorySeverityBreakdown,
  type RulesById,
  type ViolationCategory,
} from "../../utils/violationAnalytics";
import type { useApmeColorTokens } from "../../hooks/useApmeColorTokens";

type ApmeColorTokens = ReturnType<typeof useApmeColorTokens>;

const SEV_ORDER = SEVERITY_ORDER;

export type CategoryMeta = {
  key: ViolationCategory;
  label: string;
  tip: string;
};

type CardClasses = {
  card: string;
  cardClickable: string;
  categoryRow: string;
  categoryHelp: string;
};

/** Shared clickable card wrapper — guards click/keyboard with `navigationDisabled`. */
function QualitySummaryCard({
  classes,
  navigationDisabled,
  onOpen,
  children,
}: Readonly<{
  classes: Pick<CardClasses, "card" | "cardClickable">;
  navigationDisabled: boolean;
  onOpen: () => void;
  children: React.ReactNode;
}>) {
  const open = () => {
    if (!navigationDisabled) onOpen();
  };
  return (
    <Card
      variant="outlined"
      className={`${classes.card} ${navigationDisabled ? "" : classes.cardClickable}`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      }}
      role={navigationDisabled ? undefined : "button"}
      tabIndex={navigationDisabled ? undefined : 0}
      aria-disabled={navigationDisabled || undefined}
      aria-label="Quality summary — view latest scan details"
    >
      {children}
    </Card>
  );
}

export function ApmeRepositoryOverviewLoading() {
  return (
    <Box display="flex" justifyContent="center" py={2}>
      <CircularProgress size={24} />
    </Box>
  );
}

export function ApmeRepositoryOverviewFindingsUnavailable({
  classes,
  navigationDisabled,
  onViewDetails,
}: Readonly<{
  classes: Pick<CardClasses, "card">;
  navigationDisabled: boolean;
  onViewDetails: () => void;
}>) {
  return (
    <Card variant="outlined" className={classes.card}>
      <CardContent>
        <Typography variant="subtitle2" gutterBottom>
          Quality
        </Typography>
        <Typography variant="body2" color="textSecondary" paragraph>
          Quality findings unavailable. This is not a clean assessment.
        </Typography>
        <Button
          size="small"
          disabled={navigationDisabled}
          onClick={onViewDetails}
        >
          View details
        </Button>
      </CardContent>
    </Card>
  );
}

export function ApmeRepositoryOverviewNoScan({
  classes,
  navigationDisabled,
  repoUrl,
  onOpenQuality,
  onScan,
}: Readonly<{
  classes: Pick<CardClasses, "card">;
  navigationDisabled: boolean;
  repoUrl?: string | null;
  onOpenQuality?: () => void;
  onScan: () => void;
}>) {
  return (
    <Card variant="outlined" className={classes.card}>
      <CardContent>
        <Typography variant="subtitle2" gutterBottom>
          Quality
        </Typography>
        <Typography variant="body2" color="textSecondary" paragraph>
          No quality scans yet. Quality scans run on push when the APME workflow
          is configured, or use Scan to check this repository now.
        </Typography>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!repoUrl || navigationDisabled}
          startIcon={<RefreshIcon />}
          onClick={onScan}
        >
          {onOpenQuality ? "Open Quality" : "Scan"}
        </Button>
      </CardContent>
    </Card>
  );
}

export function ApmeRepositoryOverviewClean({
  classes,
  navigationDisabled,
  colorTokens,
  onOpenLatestScan,
}: Readonly<{
  classes: Pick<CardClasses, "card" | "cardClickable">;
  navigationDisabled: boolean;
  colorTokens: ApmeColorTokens;
  onOpenLatestScan: () => void;
}>) {
  return (
    <QualitySummaryCard
      classes={classes}
      navigationDisabled={navigationDisabled}
      onOpen={onOpenLatestScan}
    >
      <CardContent>
        <Typography variant="subtitle2" gutterBottom>
          Quality
        </Typography>
        <Typography
          variant="body2"
          style={{ color: colorTokens.dependencyViolation.okCheckColor }}
        >
          No violations detected on the latest scan.
        </Typography>
      </CardContent>
    </QualitySummaryCard>
  );
}

export function ApmeRepositoryOverviewViolations({
  classes,
  theme,
  colorTokens,
  navigationDisabled,
  categoryMeta,
  totalViolations,
  reportedViolations,
  fixable,
  sevCounts,
  catCounts,
  violations,
  rulesById,
  onOpenLatestScan,
}: Readonly<{
  classes: CardClasses;
  theme: Theme;
  colorTokens: ApmeColorTokens;
  navigationDisabled: boolean;
  categoryMeta: readonly CategoryMeta[];
  totalViolations: number;
  reportedViolations: number;
  fixable: number;
  sevCounts: Record<SeverityLevel, number>;
  catCounts: Record<ViolationCategory, number>;
  violations: Violation[];
  rulesById: RulesById;
  onOpenLatestScan: () => void;
}>) {
  const worstSev =
    SEV_ORDER.find((s) => sevCounts[s] > 0) ?? ("info" as SeverityLevel);

  const stopCardNavigation = (event: { stopPropagation: () => void }) => {
    event.stopPropagation();
  };

  return (
    <QualitySummaryCard
      classes={classes}
      navigationDisabled={navigationDisabled}
      onOpen={onOpenLatestScan}
    >
      <CardContent style={{ padding: "14px 16px 12px" }}>
        <Box
          display="flex"
          alignItems="center"
          justifyContent="space-between"
          mb={1}
        >
          <Typography
            style={{
              fontSize: 12,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: 0.5,
              color: theme.palette.text.secondary,
            }}
          >
            Quality
          </Typography>
          <Typography
            component="span"
            style={{
              fontSize: 12,
              color: theme.palette.primary.main,
            }}
          >
            {navigationDisabled ? "Details unavailable" : "View details →"}
          </Typography>
        </Box>

        {totalViolations < reportedViolations && (
          <Typography variant="caption" color="textSecondary">
            Partial findings: showing {totalViolations} of {reportedViolations}.
          </Typography>
        )}
        <Box
          display="flex"
          alignItems="baseline"
          style={{ gap: 6, marginBottom: 4 }}
        >
          <Typography
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: colorTokens.severity[worstSev].inlineText,
              lineHeight: 1,
            }}
          >
            {totalViolations}
          </Typography>
          <Typography
            style={{ fontSize: 12, color: theme.palette.text.secondary }}
          >
            violation{totalViolations !== 1 ? "s" : ""} · {fixable} fixable
          </Typography>
        </Box>

        <Box
          display="flex"
          style={{
            height: 6,
            borderRadius: 3,
            overflow: "hidden",
            marginBottom: 8,
          }}
        >
          {SEV_ORDER.map((sev) => {
            const count = sevCounts[sev];
            if (!count) return null;
            return (
              <Box
                key={sev}
                style={{
                  flex: count,
                  backgroundColor: colorTokens.severity[sev].barFill,
                }}
              />
            );
          })}
        </Box>

        <Box
          display="flex"
          flexWrap="wrap"
          style={{ gap: "2px 10px", marginBottom: 10 }}
        >
          {SEV_ORDER.map((sev) => {
            const count = sevCounts[sev];
            if (!count) return null;
            return (
              <Typography
                key={sev}
                style={{ fontSize: 11, color: theme.palette.text.secondary }}
              >
                <strong style={{ color: colorTokens.severity[sev].inlineText }}>
                  {count}
                </strong>{" "}
                <span style={{ textTransform: "capitalize" }}>{sev}</span>
              </Typography>
            );
          })}
        </Box>

        {categoryMeta.map(({ key, label, tip }) => {
          const count = catCounts[key] ?? 0;
          if (!count) return null;
          const catBySev = categorySeverityBreakdown(
            violations,
            key,
            rulesById,
          );
          const pct = (count / totalViolations) * 100;
          const isDark = theme.palette.type === "dark";
          const trackColor = isDark ? "rgba(255,255,255,0.08)" : "#e8e8e8";
          return (
            <Box key={key} className={classes.categoryRow}>
              <Typography
                component="span"
                style={{
                  fontSize: 12,
                  color: theme.palette.text.primary,
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 3,
                  width: 130,
                }}
              >
                {label}
                <Tooltip title={tip} arrow enterDelay={200}>
                  <IconButton
                    size="small"
                    className={classes.categoryHelp}
                    onClick={stopCardNavigation}
                    onKeyDown={stopCardNavigation}
                    aria-label={tip}
                  >
                    <HelpOutlineIcon
                      style={{
                        fontSize: 13,
                        color: theme.palette.text.disabled,
                      }}
                    />
                  </IconButton>
                </Tooltip>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    padding: "1px 7px",
                    borderRadius: 10,
                    backgroundColor: isDark
                      ? "rgba(255,255,255,0.08)"
                      : "rgba(0,0,0,0.06)",
                    color: theme.palette.text.secondary,
                    marginLeft: 2,
                  }}
                >
                  {count}
                </span>
              </Typography>
              <Box
                style={{
                  flex: 1,
                  minWidth: 0,
                  height: 6,
                  borderRadius: 3,
                  backgroundColor: trackColor,
                  overflow: "hidden",
                }}
              >
                <Box
                  display="flex"
                  style={{ height: "100%", width: `${pct}%`, borderRadius: 3 }}
                >
                  {SEV_ORDER.map((sev) => {
                    const n = catBySev[sev];
                    if (!n) return null;
                    return (
                      <Box
                        key={sev}
                        style={{
                          flex: n,
                          backgroundColor: colorTokens.severity[sev].barFill,
                        }}
                      />
                    );
                  })}
                </Box>
              </Box>
            </Box>
          );
        })}
      </CardContent>
    </QualitySummaryCard>
  );
}
