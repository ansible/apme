/*
 * Copyright Red Hat
 *
 * Fleet Rules catalog — standalone RulesPage behavior with portal chrome.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "@backstage/core-plugin-api";
import { Progress, ResponseErrorPanel } from "@backstage/core-components";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  Link,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
  makeStyles,
  useTheme,
} from "@material-ui/core";
import CloseIcon from "@material-ui/icons/Close";
import type { Rule, Severity } from "../../../common/types";
import {
  chipStyleForSeverity,
  categoryLabel,
  normalizeSeverity,
  severityLabel,
  severityLabelToProto,
  severityLevelToCatalogSeverity,
  severityProtoToLabel,
} from "../../../common/severity";
import { apmeApiRef } from "../../api";
import {
  APME_GATEWAY_UNAVAILABLE_MESSAGE,
  isApmeConnectionError,
} from "../../utils/apmeConnectionError";
import { ApmeUnavailable } from "../ApmeUnavailable";
import {
  ApmeOutlinedTableCard,
  useApmeOutlinedTableStyles,
} from "../ApmeOutlinedTable";
import { normalizeRuleId } from "../../utils/violationAnalytics";
import { toError } from "../../utils/toError";

const SEVERITY_SELECT_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: "Info" },
  { value: 2, label: "Low" },
  { value: 3, label: "Medium" },
  { value: 4, label: "High" },
  { value: 6, label: "Critical" },
];

/** Gateway/catalog 404 on reset means there was no override to delete. */
function isNotFoundError(err: unknown): boolean {
  const message = toError(err).message;
  return /\b404\b/.test(message) || /not found/i.test(message);
}

const useStyles = makeStyles((theme) => ({
  root: {
    paddingBottom: theme.spacing(3),
  },
  title: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: theme.spacing(1),
  },
  summary: {
    fontSize: 13,
    color: theme.palette.text.secondary,
    marginBottom: theme.spacing(2),
  },
  filters: {
    display: "flex",
    flexWrap: "wrap",
    gap: theme.spacing(1.5),
    marginBottom: theme.spacing(2),
    alignItems: "flex-end",
  },
  search: {
    minWidth: 260,
  },
  filterSelect: {
    minWidth: 160,
  },
  severitySelect: {
    minWidth: 110,
    maxWidth: 130,
  },
  ruleIdLink: {
    fontFamily: "monospace",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  description: {
    display: "block",
    maxWidth: 360,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
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
  footer: {
    marginTop: theme.spacing(1),
    textAlign: "right",
    fontSize: 13,
    color: theme.palette.text.secondary,
  },
  actionError: {
    color: theme.palette.error.main,
    marginBottom: theme.spacing(2),
  },
  dialogHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    paddingRight: theme.spacing(1),
  },
  detailRow: {
    display: "grid",
    gridTemplateColumns: "140px 1fr",
    gap: theme.spacing(1),
    marginBottom: theme.spacing(1.5),
    alignItems: "center",
  },
  detailTerm: {
    fontSize: 12,
    fontWeight: 600,
    color: theme.palette.text.secondary,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  },
  empty: {
    padding: theme.spacing(6),
    textAlign: "center",
    color: theme.palette.text.secondary,
  },
}));

function SeverityBadge({ severity }: Readonly<{ severity: Severity }>) {
  const classes = useStyles();
  const level = normalizeSeverity(severity);
  return (
    <span className={classes.severityChip} style={chipStyleForSeverity(level)}>
      {severityLabel(level)}
    </span>
  );
}

function effectiveSeverityInt(rule: Rule): number {
  return severityLabelToProto(rule.severity);
}

function defaultSeverityOf(rule: Rule): Severity {
  return rule.defaultSeverity ?? rule.severity;
}

/**
 * Git Repositories page tab: global APME rule catalog (enable / severity / enforce).
 */
export const ApmeRulesTab = () => {
  const classes = useStyles();
  const tableClasses = useApmeOutlinedTableStyles();
  const theme = useTheme();
  const apmeApi = useApi(apmeApiRef);
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkRuleId = searchParams.get("rule");

  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [searchText, setSearchText] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [updatingIds, setUpdatingIds] = useState<Set<string>>(() => new Set());
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);

  const startUpdating = useCallback((id: string) => {
    setUpdatingIds((prev) => new Set(prev).add(id));
  }, []);

  const stopUpdating = useCallback((id: string) => {
    setUpdatingIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const loadRules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await apmeApi.getRules();
      setRules(rows);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
      setRules([]);
    } finally {
      setLoading(false);
    }
  }, [apmeApi]);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  useEffect(() => {
    if (!deepLinkRuleId || rules.length === 0) {
      return;
    }
    const normalized = normalizeRuleId(deepLinkRuleId);
    const match = rules.find((rule) => normalizeRuleId(rule.id) === normalized);
    if (!match) {
      return;
    }
    setSelectedRule(match);
    const next = new URLSearchParams(searchParams);
    next.delete("rule");
    setSearchParams(next, { replace: true });
  }, [deepLinkRuleId, rules, searchParams, setSearchParams]);

  const categoryOptions = useMemo(() => {
    const s = new Set(rules.map((r) => r.category).filter(Boolean));
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [rules]);

  const sourceOptions = useMemo(() => {
    const s = new Set(
      rules.map((r) => r.source).filter((v): v is string => Boolean(v)),
    );
    return [...s].sort((a, b) => a.localeCompare(b));
  }, [rules]);

  const filtered = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return rules.filter((r) => {
      if (categoryFilter && r.category !== categoryFilter) return false;
      if (sourceFilter && (r.source ?? "") !== sourceFilter) return false;
      if (!q) return true;
      return (
        r.id.toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q) ||
        r.name.toLowerCase().includes(q)
      );
    });
  }, [rules, searchText, categoryFilter, sourceFilter]);

  const overrideCount = useMemo(
    () => rules.filter((r) => r.hasOverride).length,
    [rules],
  );

  const patchRule = useCallback((ruleId: string, patch: Partial<Rule>) => {
    setRules((cur) =>
      cur.map((r) => (r.id === ruleId ? { ...r, ...patch } : r)),
    );
    setSelectedRule((cur) => (cur?.id === ruleId ? { ...cur, ...patch } : cur));
  }, []);

  const handleEnabledChange = useCallback(
    async (rule: Rule, enabled: boolean) => {
      const prev = {
        enabled: rule.enabled,
        hasOverride: rule.hasOverride,
      };
      setActionError(null);
      patchRule(rule.id, { enabled, hasOverride: true });
      startUpdating(rule.id);
      try {
        await apmeApi.updateRuleConfig(rule.id, {
          enabled_override: enabled,
        });
      } catch (err) {
        patchRule(rule.id, prev);
        setActionError(toError(err));
      } finally {
        stopUpdating(rule.id);
      }
    },
    [apmeApi, patchRule, startUpdating, stopUpdating],
  );

  const handleEnforcedChange = useCallback(
    async (rule: Rule, enforced: boolean) => {
      const prev = {
        enforced: rule.enforced,
        hasOverride: rule.hasOverride,
      };
      setActionError(null);
      patchRule(rule.id, { enforced, hasOverride: true });
      startUpdating(rule.id);
      try {
        await apmeApi.updateRuleConfig(rule.id, { enforced });
      } catch (err) {
        patchRule(rule.id, prev);
        setActionError(toError(err));
      } finally {
        stopUpdating(rule.id);
      }
    },
    [apmeApi, patchRule, startUpdating, stopUpdating],
  );

  const handleSeverityChange = useCallback(
    async (rule: Rule, severityInt: number) => {
      if (severityInt === effectiveSeverityInt(rule)) return;
      const prev = {
        severity: rule.severity,
        hasOverride: rule.hasOverride,
      };
      const nextSeverity = severityLevelToCatalogSeverity(
        severityProtoToLabel(severityInt),
      );
      setActionError(null);
      patchRule(rule.id, {
        severity: nextSeverity,
        hasOverride: true,
      });
      startUpdating(rule.id);
      try {
        await apmeApi.updateRuleConfig(rule.id, {
          severity_override: severityInt,
        });
      } catch (err) {
        patchRule(rule.id, prev);
        setActionError(toError(err));
      } finally {
        stopUpdating(rule.id);
      }
    },
    [apmeApi, patchRule, startUpdating, stopUpdating],
  );

  const handleResetOverride = useCallback(
    async (ruleId: string) => {
      setActionError(null);
      startUpdating(ruleId);
      try {
        await apmeApi.deleteRuleConfig(ruleId);
        await loadRules();
        setSelectedRule((cur) => {
          if (cur?.id !== ruleId) return cur;
          return null;
        });
      } catch (err) {
        // 404 = no override to delete; ignore. Surface other failures.
        if (!isNotFoundError(err)) {
          setActionError(toError(err));
        }
      } finally {
        stopUpdating(ruleId);
      }
    },
    [apmeApi, loadRules, startUpdating, stopUpdating],
  );

  if (error && isApmeConnectionError(error.message)) {
    return <ApmeUnavailable message={APME_GATEWAY_UNAVAILABLE_MESSAGE} />;
  }

  if (error && rules.length === 0 && !loading) {
    return <ResponseErrorPanel error={error} />;
  }

  return (
    <Box className={classes.root}>
      <Typography className={classes.title}>Rules</Typography>
      <Typography className={classes.summary}>
        <strong>{rules.length}</strong> registered
        {" · "}
        <strong>{overrideCount}</strong> with overrides
      </Typography>

      {actionError && (
        <Typography
          variant="body2"
          className={classes.actionError}
          role="alert"
        >
          {actionError.message}
        </Typography>
      )}

      <Box className={classes.filters}>
        <TextField
          className={classes.search}
          size="small"
          variant="outlined"
          label="Search"
          placeholder="Rule ID or description"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
        />
        <FormControl
          className={classes.filterSelect}
          size="small"
          variant="outlined"
        >
          <InputLabel id="rules-category-filter">Category</InputLabel>
          <Select
            labelId="rules-category-filter"
            label="Category"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(String(e.target.value))}
          >
            <MenuItem value="">All categories</MenuItem>
            {categoryOptions.map((c) => (
              <MenuItem key={c} value={c}>
                {categoryLabel(c)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl
          className={classes.filterSelect}
          size="small"
          variant="outlined"
        >
          <InputLabel id="rules-source-filter">Source</InputLabel>
          <Select
            labelId="rules-source-filter"
            label="Source"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(String(e.target.value))}
          >
            <MenuItem value="">All sources</MenuItem>
            {sourceOptions.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      {loading && <Progress />}
      {!loading && filtered.length === 0 && (
        <Typography className={classes.empty}>
          {rules.length === 0
            ? "No rules in the catalog yet. When the engine registers with APME, rules appear here."
            : "No rules match the current filters."}
        </Typography>
      )}
      {!loading && filtered.length > 0 && (
        <ApmeOutlinedTableCard>
          <table className={tableClasses.table} aria-label="Rule catalog">
            <thead>
              <tr>
                <th>Rule ID</th>
                <th>Description</th>
                <th>Source</th>
                <th>Category</th>
                <th>Default severity</th>
                <th>Effective severity</th>
                <th>Status</th>
                <th>Enforced</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((rule) => {
                const updating = updatingIds.has(rule.id);
                return (
                  <tr key={rule.id}>
                    <td>
                      <Link
                        component="button"
                        type="button"
                        className={classes.ruleIdLink}
                        onClick={() => setSelectedRule(rule)}
                        underline="hover"
                        color="primary"
                      >
                        {rule.id}
                      </Link>
                    </td>
                    <td>
                      <span
                        className={classes.description}
                        title={rule.description}
                      >
                        {rule.description || "—"}
                      </span>
                    </td>
                    <td>{rule.source || "—"}</td>
                    <td>{categoryLabel(rule.category)}</td>
                    <td>
                      <SeverityBadge severity={defaultSeverityOf(rule)} />
                    </td>
                    <td>
                      <FormControl
                        className={classes.severitySelect}
                        size="small"
                        variant="outlined"
                      >
                        <Select
                          value={effectiveSeverityInt(rule)}
                          disabled={updating}
                          onChange={(e) => {
                            void handleSeverityChange(
                              rule,
                              Number(e.target.value),
                            );
                          }}
                          inputProps={{
                            "aria-label": `Severity for ${rule.id}`,
                          }}
                        >
                          {SEVERITY_SELECT_OPTIONS.map((opt) => (
                            <MenuItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    </td>
                    <td>
                      <Switch
                        size="small"
                        color="primary"
                        checked={rule.enabled}
                        disabled={updating}
                        onChange={(_e, checked) => {
                          void handleEnabledChange(rule, checked);
                        }}
                        inputProps={{
                          "aria-label": `Enable ${rule.id}`,
                        }}
                      />
                    </td>
                    <td>
                      <Switch
                        size="small"
                        color="primary"
                        checked={Boolean(rule.enforced)}
                        disabled={updating}
                        onChange={(_e, checked) => {
                          void handleEnforcedChange(rule, checked);
                        }}
                        inputProps={{
                          "aria-label": `Enforce ${rule.id}`,
                        }}
                      />
                    </td>
                    <td>
                      {rule.hasOverride ? (
                        <Button
                          size="small"
                          color="primary"
                          disabled={updating}
                          onClick={() => void handleResetOverride(rule.id)}
                        >
                          Reset
                        </Button>
                      ) : (
                        <Typography
                          component="span"
                          style={{
                            color: theme.palette.text.disabled,
                            fontSize: 12,
                          }}
                        >
                          —
                        </Typography>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ApmeOutlinedTableCard>
      )}

      <Typography className={classes.footer}>
        {filtered.length} rule{filtered.length !== 1 ? "s" : ""} shown
      </Typography>

      <Dialog
        open={Boolean(selectedRule)}
        onClose={() => setSelectedRule(null)}
        maxWidth="sm"
        fullWidth
      >
        {selectedRule && (
          <>
            <DialogTitle disableTypography className={classes.dialogHeader}>
              <Typography variant="h6" style={{ fontFamily: "monospace" }}>
                Rule: {selectedRule.id}
              </Typography>
              {/* eslint-disable-next-line a11y/click-events-have-key-events -- IconButton renders a native button with keyboard activation. */}
              <IconButton
                aria-label="Close rule detail"
                onClick={() => setSelectedRule(null)}
                size="small"
              >
                <CloseIcon fontSize="small" />
              </IconButton>
            </DialogTitle>
            <DialogContent dividers>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>
                  Description
                </Typography>
                <Typography>{selectedRule.description || "—"}</Typography>
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>Category</Typography>
                <Typography>{categoryLabel(selectedRule.category)}</Typography>
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>Source</Typography>
                <Typography>{selectedRule.source || "—"}</Typography>
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>
                  Default severity
                </Typography>
                <SeverityBadge severity={defaultSeverityOf(selectedRule)} />
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>
                  Effective severity
                </Typography>
                <FormControl
                  className={classes.severitySelect}
                  size="small"
                  variant="outlined"
                >
                  <Select
                    value={effectiveSeverityInt(selectedRule)}
                    disabled={updatingIds.has(selectedRule.id)}
                    onChange={(e) => {
                      void handleSeverityChange(
                        selectedRule,
                        Number(e.target.value),
                      );
                    }}
                    inputProps={{
                      "aria-label": `Override severity for ${selectedRule.id}`,
                    }}
                  >
                    {SEVERITY_SELECT_OPTIONS.map((opt) => (
                      <MenuItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>Enabled</Typography>
                <Switch
                  size="small"
                  color="primary"
                  checked={selectedRule.enabled}
                  disabled={updatingIds.has(selectedRule.id)}
                  onChange={(_e, checked) => {
                    void handleEnabledChange(selectedRule, checked);
                  }}
                  inputProps={{
                    "aria-label": `Enable ${selectedRule.id}`,
                  }}
                />
              </Box>
              <Box className={classes.detailRow}>
                <Typography className={classes.detailTerm}>Enforced</Typography>
                <Switch
                  size="small"
                  color="primary"
                  checked={Boolean(selectedRule.enforced)}
                  disabled={updatingIds.has(selectedRule.id)}
                  onChange={(_e, checked) => {
                    void handleEnforcedChange(selectedRule, checked);
                  }}
                  inputProps={{
                    "aria-label": `Enforce ${selectedRule.id}`,
                  }}
                />
              </Box>
            </DialogContent>
            <DialogActions>
              {selectedRule.hasOverride ? (
                <Button
                  color="primary"
                  disabled={updatingIds.has(selectedRule.id)}
                  onClick={() => void handleResetOverride(selectedRule.id)}
                >
                  Reset override
                </Button>
              ) : null}
              <Button onClick={() => setSelectedRule(null)}>Close</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Box>
  );
};
