/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  CircularProgress,
  Tooltip,
  Typography,
  makeStyles,
  useTheme,
} from "@material-ui/core";
import CheckCircleOutlineIcon from "@material-ui/icons/CheckCircleOutline";
import HelpOutlineIcon from "@material-ui/icons/HelpOutline";
import RefreshIcon from "@material-ui/icons/Refresh";
import type { GitRepositoryDetailTabContext } from "@ansible/backstage-rhaap-common/gitRepositoriesExtensions";
import { useApmeProjectContext } from "../../hooks/useApmeProjectContext";
import { ApmeUnavailable } from "../ApmeUnavailable";
import {
  ApmeOutlinedTableCard,
  useApmeOutlinedTableStyles,
} from "../ApmeOutlinedTable";
import {
  APME_GATEWAY_UNAVAILABLE_MESSAGE,
  isApmeConnectionError,
} from "../../utils/apmeConnectionError";
import {
  collectionViolationCount,
  pythonPackageViolationCount,
  violationsForCollection,
  violationsForPythonPackage,
} from "../../utils/violationAnalytics";
import { useViolationAcknowledge } from "../../hooks/useViolationAcknowledge";
import { useApmeColorTokens } from "../../hooks/useApmeColorTokens";
import { ViolationDetailModal } from "../ViolationDetailModal";

function activateOnKey(e: KeyboardEvent, action: () => void): void {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    action();
  }
}

const useStyles = makeStyles((theme) => ({
  root: {
    padding: "24px 0",
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: 600,
    marginBottom: 12,
    marginTop: 8,
  },
  sectionCard: {
    marginBottom: 24,
  },
  sourceBadge: {
    fontSize: 10,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    padding: "1px 6px",
    borderRadius: 3,
    letterSpacing: 0.3,
  },
  noData: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 40,
    color: theme.palette.text.secondary,
    fontSize: 14,
    gap: 8,
  },
}));

type ModalTarget = {
  title: string;
  subtitle?: string;
  violations: ReturnType<typeof violationsForCollection>;
} | null;

export interface DependenciesTabProps {
  context: GitRepositoryDetailTabContext;
}

export const DependenciesTab = ({ context }: DependenciesTabProps) => {
  const classes = useStyles();
  const tableClasses = useApmeOutlinedTableStyles();
  const theme = useTheme();
  const colorTokens = useApmeColorTokens();
  const [, setSearchParams] = useSearchParams();
  const [modalTarget, setModalTarget] = useState<ModalTarget>(null);
  const pendingViolationsRefreshRef = useRef(false);
  const ctx = useApmeProjectContext(context.entity);

  const handleAcknowledgeChanged = useCallback(() => {
    if (modalTarget) {
      pendingViolationsRefreshRef.current = true;
      return;
    }
    ctx.refreshViolations();
  }, [modalTarget, ctx]);

  const { acknowledge, unacknowledge, acknowledgingId, isAcknowledged } =
    useViolationAcknowledge(
      ctx.project?.id,
      handleAcknowledgeChanged,
      ctx.violations,
    );

  const handleCloseModal = useCallback(() => {
    setModalTarget(null);
    if (pendingViolationsRefreshRef.current) {
      pendingViolationsRefreshRef.current = false;
      ctx.refreshViolations();
    }
  }, [ctx]);

  const activeViolations = useMemo(
    () => ctx.violations.filter((v) => !isAcknowledged(v)),
    [ctx.violations, isAcknowledged],
  );

  const navigateToQuality = () => {
    const params = new URLSearchParams(window.location.search);
    params.set("tab", "quality");
    params.set("category", "dependencies");
    setSearchParams(params, { replace: true });
  };

  const collections = useMemo(() => {
    const rows = [...(ctx.dependencies?.collections ?? [])];
    return rows.sort(
      (a, b) =>
        collectionViolationCount(activeViolations, b, ctx.rulesById) -
        collectionViolationCount(activeViolations, a, ctx.rulesById),
    );
  }, [ctx.dependencies, activeViolations, ctx.rulesById]);

  const pythonPkgs = useMemo(() => {
    const rows = [...(ctx.dependencies?.python_packages ?? [])];
    return rows.sort(
      (a, b) =>
        pythonPackageViolationCount(activeViolations, b.name, ctx.rulesById) -
        pythonPackageViolationCount(activeViolations, a.name, ctx.rulesById),
    );
  }, [ctx.dependencies, activeViolations, ctx.rulesById]);

  if (
    ctx.loading ||
    ctx.dependenciesLoading ||
    (ctx.violationsLoading && ctx.violations.length === 0)
  ) {
    return (
      <Box className={classes.noData}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (ctx.error && isApmeConnectionError(ctx.error.message)) {
    return <ApmeUnavailable message={APME_GATEWAY_UNAVAILABLE_MESSAGE} />;
  }

  if (!ctx.project) {
    return (
      <Box className={classes.noData} flexDirection="column">
        <Typography>No quality scans yet</Typography>
        <Typography
          variant="body2"
          color="textSecondary"
          style={{ marginBottom: 8 }}
        >
          Run a scan to discover dependencies for this repository.
        </Typography>
        <Button
          size="small"
          variant="contained"
          color="primary"
          startIcon={<RefreshIcon />}
          onClick={navigateToQuality}
        >
          Scan
        </Button>
      </Box>
    );
  }

  if (!ctx.dependencies) {
    return (
      <Box className={classes.noData} flexDirection="column">
        <Typography>Run a quality scan to discover dependencies.</Typography>
      </Box>
    );
  }

  const lastScanned = ctx.project.last_scanned_at
    ? new Date(ctx.project.last_scanned_at).toLocaleString()
    : "—";

  return (
    <Box className={classes.root}>
      <Box
        display="flex"
        alignItems="center"
        style={{ gap: 10, marginBottom: 6 }}
      >
        <Typography style={{ fontSize: 13 }}>
          <strong>{collections.length}</strong> collection
          {collections.length !== 1 ? "s" : ""}
          {" · "}
          <strong>{pythonPkgs.length}</strong> Python package
          {pythonPkgs.length !== 1 ? "s" : ""}
        </Typography>
      </Box>

      <Box
        display="flex"
        alignItems="center"
        style={{ gap: 6, marginBottom: 20 }}
      >
        <Typography
          style={{ fontSize: 12, color: theme.palette.text.secondary }}
        >
          Discovered from latest{" "}
          <button
            type="button"
            onClick={navigateToQuality}
            onKeyDown={(e) => activateOnKey(e, navigateToQuality)}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              font: "inherit",
              color: "inherit",
              textDecoration: "underline",
              cursor: "pointer",
            }}
          >
            quality scan
          </button>
          {" · "}
          {lastScanned}
        </Typography>
        <Tooltip title="Dependencies are detected from the latest quality scan of this repository.">
          <HelpOutlineIcon
            style={{ fontSize: 14, opacity: 0.5, cursor: "help" }}
          />
        </Tooltip>
      </Box>

      <Typography className={classes.sectionTitle}>
        Collections ({collections.length})
      </Typography>
      <Box className={classes.sectionCard}>
        <ApmeOutlinedTableCard>
          {collections.length === 0 ? (
            <Typography className={classes.noData}>
              No collections detected
            </Typography>
          ) : (
            <table className={tableClasses.table}>
              <thead>
                <tr>
                  <th>FQCN</th>
                  <th>Version</th>
                  <th>Source</th>
                  <th style={{ textAlign: "right" }}>Violations</th>
                </tr>
              </thead>
              <tbody>
                {collections.map((c) => {
                  const srcStyle =
                    colorTokens.dependencySource[c.source] ??
                    colorTokens.dependencySource.specified;
                  const vCount = collectionViolationCount(
                    activeViolations,
                    c,
                    ctx.rulesById,
                  );
                  const matched = violationsForCollection(
                    ctx.violations,
                    c.fqcn,
                    ctx.rulesById,
                  );
                  return (
                    <tr key={c.fqcn}>
                      <td>
                        <Typography
                          style={{
                            fontFamily: "monospace",
                            fontSize: 13,
                            fontWeight: 500,
                          }}
                        >
                          {c.fqcn}
                        </Typography>
                      </td>
                      <td>
                        <Typography
                          style={{
                            fontFamily: "monospace",
                            fontSize: 12,
                            color: theme.palette.text.secondary,
                          }}
                        >
                          {c.version}
                        </Typography>
                      </td>
                      <td>
                        <span
                          className={classes.sourceBadge}
                          style={{
                            backgroundColor: srcStyle.background,
                            color: srcStyle.text,
                          }}
                        >
                          {c.source}
                        </span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {vCount > 0 ? (
                          <button
                            type="button"
                            onClick={() =>
                              setModalTarget({
                                title: `${c.fqcn} ${c.version}`,
                                subtitle: `${vCount} violation${vCount !== 1 ? "s" : ""}`,
                                violations: matched,
                              })
                            }
                            onKeyDown={(e) =>
                              activateOnKey(e, () =>
                                setModalTarget({
                                  title: `${c.fqcn} ${c.version}`,
                                  subtitle: `${vCount} violation${vCount !== 1 ? "s" : ""}`,
                                  violations: matched,
                                }),
                              )
                            }
                            style={{
                              border: "none",
                              cursor: "pointer",
                              fontSize: 11,
                              fontWeight: 600,
                              padding: "1px 7px",
                              borderRadius: 10,
                              backgroundColor:
                                colorTokens.dependencyViolation
                                  .countPillBackground,
                              color:
                                colorTokens.dependencyViolation.countPillText,
                            }}
                          >
                            {vCount}
                          </button>
                        ) : (
                          <CheckCircleOutlineIcon
                            style={{
                              fontSize: 12,
                              width: 12,
                              height: 12,
                              color:
                                colorTokens.dependencyViolation.okCheckColor,
                              opacity: 0.7,
                            }}
                            fontSize="inherit"
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </ApmeOutlinedTableCard>
      </Box>

      <Typography className={classes.sectionTitle}>
        Python packages ({pythonPkgs.length})
      </Typography>
      <ApmeOutlinedTableCard>
        {pythonPkgs.length === 0 ? (
          <Typography className={classes.noData}>
            No Python packages detected
          </Typography>
        ) : (
          <table className={tableClasses.table}>
            <thead>
              <tr>
                <th>Package</th>
                <th>Version</th>
                <th style={{ textAlign: "right" }}>Violations</th>
              </tr>
            </thead>
            <tbody>
              {pythonPkgs.map((p) => {
                const vCount = pythonPackageViolationCount(
                  activeViolations,
                  p.name,
                  ctx.rulesById,
                );
                const matched = violationsForPythonPackage(
                  ctx.violations,
                  p.name,
                  ctx.rulesById,
                );
                return (
                  <tr key={p.name}>
                    <td>
                      <Typography
                        style={{
                          fontFamily: "monospace",
                          fontSize: 13,
                          fontWeight: 500,
                        }}
                      >
                        {p.name}
                      </Typography>
                    </td>
                    <td>
                      <Typography
                        style={{
                          fontFamily: "monospace",
                          fontSize: 12,
                          color: theme.palette.text.secondary,
                        }}
                      >
                        {p.version}
                      </Typography>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {vCount > 0 ? (
                        <button
                          type="button"
                          onClick={() =>
                            setModalTarget({
                              title: p.name,
                              subtitle: `${vCount} violation${vCount !== 1 ? "s" : ""}`,
                              violations: matched,
                            })
                          }
                          onKeyDown={(e) =>
                            activateOnKey(e, () =>
                              setModalTarget({
                                title: p.name,
                                subtitle: `${vCount} violation${vCount !== 1 ? "s" : ""}`,
                                violations: matched,
                              }),
                            )
                          }
                          style={{
                            border: "none",
                            cursor: "pointer",
                            fontSize: 11,
                            fontWeight: 600,
                            padding: "1px 7px",
                            borderRadius: 10,
                            backgroundColor:
                              colorTokens.dependencyViolation
                                .countPillBackground,
                            color:
                              colorTokens.dependencyViolation.countPillText,
                          }}
                        >
                          {vCount}
                        </button>
                      ) : (
                        <CheckCircleOutlineIcon
                          style={{
                            fontSize: 12,
                            width: 12,
                            height: 12,
                            color: colorTokens.dependencyViolation.okCheckColor,
                            opacity: 0.7,
                          }}
                          fontSize="inherit"
                        />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </ApmeOutlinedTableCard>

      {modalTarget && (
        <ViolationDetailModal
          open
          title={modalTarget.title}
          subtitle={modalTarget.subtitle}
          violations={modalTarget.violations}
          onClose={handleCloseModal}
          onAcknowledge={acknowledge}
          onUnacknowledge={unacknowledge}
          acknowledgingId={acknowledgingId}
          isAcknowledged={isAcknowledged}
        />
      )}
    </Box>
  );
};
