/*
 * Copyright Red Hat
 *
 * Host-side suppressions for Quality triage (US-007) and Dependencies tab.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import type {
  CreateSuppressionRequest,
  Suppression,
  Violation,
} from "../../common/types";
import { apmeApiRef } from "../api";

export type AcknowledgeLabelVariant = "wontFix" | "acknowledge";

export function acknowledgeButtonLabel(
  acknowledgingId: number | null | undefined,
  violationId: number,
  isAcknowledged: boolean,
  variant: AcknowledgeLabelVariant = "acknowledge",
): string {
  if (acknowledgingId === violationId) return "Saving…";
  if (variant === "acknowledge") {
    return isAcknowledged ? "Acknowledged" : "Acknowledge";
  }
  return isAcknowledged ? "Won't be fixed" : "Won't fix";
}

export function isDuplicateSuppressionError(err: unknown): boolean {
  if (
    err !== null &&
    err !== undefined &&
    typeof err === "object" &&
    "status" in err
  ) {
    const status = (err as { status: number }).status;
    if (status === 409) return true;
  }
  if (!(err instanceof Error)) return false;
  const msg = err.message.toLowerCase();
  return (
    msg.includes("409") ||
    msg.includes("already exists") ||
    /apme api conflict/i.test(err.message)
  );
}

function buildSuppressionRequest(
  violation: Violation,
  scope: string,
): CreateSuppressionRequest {
  const yaml = violation.original_yaml ?? "";
  const hasYaml = Boolean(yaml.trim());
  return {
    rule_id: violation.rule_id,
    original_yaml: hasYaml ? yaml : "",
    fingerprint_mode: hasYaml ? "full" : "rule_only",
    scope,
    reason: "Acknowledged via Quality triage",
  };
}

/** Prefer a single unambiguous suppression for this violation's rule. */
export function pickSuppressionForViolation(
  list: Suppression[],
  violation: Violation,
): Suppression | undefined {
  const matches = list.filter((s) => s.rule_id === violation.rule_id);
  if (matches.length === 1) {
    return matches[0];
  }
  return undefined;
}

export function useViolationAcknowledge(
  projectId: string | undefined,
  onChanged?: () => void,
  violations?: Violation[],
) {
  const apmeApi = useApi(apmeApiRef);
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null);
  const [suppressionByViolation, setSuppressionByViolation] = useState<
    Map<number, number>
  >(new Map());
  const [optimisticAcknowledgedIds, setOptimisticAcknowledgedIds] = useState<
    Set<number>
  >(() => new Set());
  const [ackError, setAckError] = useState<string | null>(null);

  useEffect(() => {
    if (!violations?.length) return;
    setOptimisticAcknowledgedIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const v of violations) {
        if (v.suppressed && next.delete(v.id)) {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [violations]);

  const markAcknowledged = useCallback((violationId: number) => {
    setOptimisticAcknowledgedIds((prev) => new Set(prev).add(violationId));
  }, []);

  const isAcknowledged = useCallback(
    (violation: Violation) =>
      violation.suppressed === true ||
      optimisticAcknowledgedIds.has(violation.id),
    [optimisticAcknowledgedIds],
  );

  const acknowledgedIds = useMemo(() => {
    const ids = new Set(optimisticAcknowledgedIds);
    if (violations) {
      for (const v of violations) {
        if (v.suppressed) ids.add(v.id);
      }
    }
    return ids;
  }, [optimisticAcknowledgedIds, violations]);

  const cacheSuppressionId = useCallback(
    async (violation: Violation) => {
      if (!projectId) return;
      const list = await apmeApi.getSuppressions(`project:${projectId}`);
      const match = pickSuppressionForViolation(list, violation);
      if (match) {
        setSuppressionByViolation((prev) =>
          new Map(prev).set(violation.id, match.id),
        );
      }
    },
    [apmeApi, projectId],
  );

  const acknowledge = useCallback(
    async (violation: Violation) => {
      if (!projectId) return;
      setAckError(null);
      setAcknowledgingId(violation.id);
      try {
        const suppression = await apmeApi.createSuppression(
          buildSuppressionRequest(violation, `project:${projectId}`),
        );
        setSuppressionByViolation((prev) =>
          new Map(prev).set(violation.id, suppression.id),
        );
        markAcknowledged(violation.id);
        onChanged?.();
      } catch (err) {
        if (isDuplicateSuppressionError(err)) {
          markAcknowledged(violation.id);
          try {
            await cacheSuppressionId(violation);
          } catch {
            // best-effort cache for later unacknowledge
          }
          onChanged?.();
          return;
        }
        setAckError(
          `Failed to acknowledge: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      } finally {
        setAcknowledgingId(null);
      }
    },
    [apmeApi, cacheSuppressionId, markAcknowledged, onChanged, projectId],
  );

  const unacknowledge = useCallback(
    async (violation: Violation) => {
      if (!projectId) return;
      setAckError(null);
      setAcknowledgingId(violation.id);
      try {
        let suppressionId = suppressionByViolation.get(violation.id);
        if (suppressionId === undefined) {
          const list = await apmeApi.getSuppressions(`project:${projectId}`);
          const match = pickSuppressionForViolation(list, violation);
          if (!match) {
            const sameRule = list.filter(
              (s) => s.rule_id === violation.rule_id,
            );
            if (sameRule.length > 1) {
              setAckError(
                `Cannot unacknowledge: multiple suppressions share rule ${violation.rule_id}`,
              );
              return;
            }
            setAckError("No matching suppression found to remove");
            return;
          }
          suppressionId = match.id;
        }
        await apmeApi.deleteSuppression(suppressionId);
        setSuppressionByViolation((prev) => {
          const next = new Map(prev);
          next.delete(violation.id);
          return next;
        });
        setOptimisticAcknowledgedIds((prev) => {
          const next = new Set(prev);
          next.delete(violation.id);
          return next;
        });
        onChanged?.();
      } catch (err) {
        setAckError(
          `Failed to unacknowledge: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      } finally {
        setAcknowledgingId(null);
      }
    },
    [apmeApi, onChanged, projectId, suppressionByViolation],
  );

  const resetAcknowledged = useCallback(() => {
    setOptimisticAcknowledgedIds(new Set());
    setSuppressionByViolation(new Map());
    setAckError(null);
  }, []);

  const clearAckError = useCallback(() => setAckError(null), []);

  return {
    acknowledge,
    unacknowledge,
    acknowledgingId,
    pendingId: acknowledgingId,
    isAcknowledged,
    acknowledgedIds,
    ackError,
    resetAcknowledged,
    clearAckError,
  };
}
