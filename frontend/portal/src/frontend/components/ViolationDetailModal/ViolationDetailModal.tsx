/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import { useLayoutEffect, useMemo, useRef } from "react";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Tooltip,
  Typography,
  makeStyles,
} from "@material-ui/core";
import CheckCircleOutlineIcon from "@material-ui/icons/CheckCircleOutline";
import CloseIcon from "@material-ui/icons/Close";
import type { Violation } from "../../../common/types";
import {
  severityLabel,
  normalizeSeverity,
  categoryLabel,
} from "../../../common/severity";
import { useApmeColorTokens } from "../../hooks/useApmeColorTokens";
import { getViolationCategory } from "../../utils/violationAnalytics";
import { acknowledgeButtonLabel } from "../../hooks/useViolationAcknowledge";
import { DiffView } from "../DiffView";

const useStyles = makeStyles((theme) => ({
  dialogPaper: {
    maxWidth: 680,
    width: "100%",
    borderRadius: 12,
  },
  header: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    padding: "20px 24px 16px",
    borderBottom: `1px solid ${theme.palette.divider}`,
  },
  body: {
    padding: "0 24px 24px",
    maxHeight: "60vh",
    overflowY: "auto",
  },
  violationRow: {
    padding: "12px 0",
    borderBottom: `1px solid ${theme.palette.divider}`,
    "&:last-child": { borderBottom: "none" },
  },
  acknowledgedRow: {
    opacity: 0.65,
  },
  severityChip: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 3,
    fontSize: 10,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  },
  ruleId: {
    fontSize: 11,
    fontFamily: "monospace",
    color: theme.palette.text.secondary,
    backgroundColor:
      theme.palette.type === "dark" ? "rgba(255,255,255,0.08)" : "#f0f0f0",
    padding: "1px 5px",
    borderRadius: 3,
  },
  rowFooter: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 8,
    gap: 8,
    flexWrap: "wrap",
  },
}));

export interface ViolationDetailModalProps {
  open: boolean;
  title: string;
  subtitle?: string;
  violations: Violation[];
  onClose: () => void;
  onAcknowledge?: (violation: Violation) => Promise<void>;
  onUnacknowledge?: (violation: Violation) => Promise<void>;
  acknowledgingId?: number | null;
  isAcknowledged?: (violation: Violation) => boolean;
}

export const ViolationDetailModal = ({
  open,
  title,
  subtitle,
  violations,
  onClose,
  onAcknowledge,
  onUnacknowledge,
  acknowledgingId = null,
  isAcknowledged: isAcknowledgedProp,
}: ViolationDetailModalProps) => {
  const classes = useStyles();
  const colorTokens = useApmeColorTokens();
  const bodyRef = useRef<HTMLDivElement>(null);
  const scrollAnchorIdRef = useRef<number | null>(null);
  const canAcknowledge = Boolean(onAcknowledge || onUnacknowledge);
  const isAcknowledged = useMemo(
    () => isAcknowledgedProp ?? ((v: Violation) => v.suppressed === true),
    [isAcknowledgedProp],
  );

  const displayViolations = useMemo(() => {
    return [...violations].sort((a, b) => {
      const aAck = isAcknowledged(a) ? 1 : 0;
      const bAck = isAcknowledged(b) ? 1 : 0;
      if (aAck !== bAck) return aAck - bAck;
      return a.id - b.id;
    });
  }, [violations, isAcknowledged]);

  useLayoutEffect(() => {
    const anchorId = scrollAnchorIdRef.current;
    if (anchorId === null || !bodyRef.current) return;
    scrollAnchorIdRef.current = null;
    const row = bodyRef.current.querySelector(
      `[data-violation-id="${anchorId}"]`,
    );
    row?.scrollIntoView({ block: "nearest" });
  }, [displayViolations, acknowledgingId]);

  const handleToggle = async (violation: Violation) => {
    scrollAnchorIdRef.current = violation.id;
    if (isAcknowledged(violation)) {
      if (onUnacknowledge) await onUnacknowledge(violation);
      return;
    }
    if (onAcknowledge) await onAcknowledge(violation);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      classes={{ paper: classes.dialogPaper }}
    >
      <Box className={classes.header}>
        <Box>
          <Typography variant="h6" style={{ fontSize: 16, fontWeight: 600 }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography variant="body2" color="textSecondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        {/* eslint-disable-next-line a11y/click-events-have-key-events -- IconButton renders a native button with keyboard activation. */}
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <CloseIcon />
        </IconButton>
      </Box>
      <div ref={bodyRef} className={classes.body}>
        {displayViolations.map((v) => {
          const sev = normalizeSeverity(v.level);
          const tokens = colorTokens.severity[sev];
          const isAcknowledgedRow = isAcknowledged(v);
          return (
            <Box
              key={v.id}
              data-violation-id={v.id}
              className={`${classes.violationRow} ${isAcknowledgedRow ? classes.acknowledgedRow : ""}`}
            >
              <Box
                display="flex"
                alignItems="center"
                style={{ gap: 8, flexWrap: "wrap" }}
              >
                <span
                  className={classes.severityChip}
                  style={{
                    backgroundColor: tokens.pillBackground,
                    color: tokens.pillText,
                  }}
                >
                  {severityLabel(v.level)}
                </span>
                <span className={classes.ruleId}>{v.rule_id}</span>
                <Typography variant="body2" style={{ flex: 1 }}>
                  {v.message}
                </Typography>
              </Box>
              <Box className={classes.rowFooter}>
                <Typography variant="caption" color="textSecondary">
                  {categoryLabel(getViolationCategory(v))} · {v.file || "—"}
                  {v.line ? `:${v.line}` : ""}
                  {v.path && v.validator_source === "collection_health"
                    ? ` · ${v.path}`
                    : ""}
                </Typography>
                {canAcknowledge && (
                  <Tooltip
                    title={
                      isAcknowledgedRow
                        ? "Show again for this repo"
                        : "Acknowledge — hide for this repo; does not fix the issue"
                    }
                  >
                    <Button
                      size="small"
                      variant="text"
                      color={isAcknowledgedRow ? "default" : "primary"}
                      style={{ fontSize: 12, minWidth: 0 }}
                      startIcon={
                        isAcknowledgedRow ? (
                          <CheckCircleOutlineIcon style={{ fontSize: 14 }} />
                        ) : undefined
                      }
                      disabled={acknowledgingId === v.id}
                      onClick={() => void handleToggle(v)}
                    >
                      {acknowledgeButtonLabel(
                        acknowledgingId,
                        v.id,
                        isAcknowledgedRow,
                        "acknowledge",
                      )}
                    </Button>
                  </Tooltip>
                )}
              </Box>
              {(v.original_yaml || v.fixed_yaml) && (
                <Box mt={1}>
                  <DiffView
                    before={v.original_yaml ?? ""}
                    after={v.fixed_yaml ?? v.original_yaml ?? ""}
                  />
                </Box>
              )}
            </Box>
          );
        })}
        {displayViolations.length === 0 && (
          <Typography
            variant="body2"
            color="textSecondary"
            style={{ padding: "16px 0" }}
          >
            No violations to display.
          </Typography>
        )}
      </div>
    </Dialog>
  );
};
