/*
 * Copyright Red Hat
 */

import type { Activity } from "./types";
import type { ActivityPortalOutcome } from "./resolveScanTarget";

/** Merge portal-persisted submit outcomes into gateway activity rows. */
export function mergeActivityPortalOutcomes(
  activities: Activity[],
  outcomes?: Record<string, ActivityPortalOutcome>,
): Activity[] {
  if (!outcomes || Object.keys(outcomes).length === 0) {
    return activities;
  }

  return activities.map((activity) => {
    const stored = outcomes[activity.scan_id];
    if (!stored) {
      return activity;
    }

    return {
      ...activity,
      branch_name: activity.branch_name ?? stored.branch_name ?? null,
      pr_url: activity.pr_url ?? stored.pr_url ?? null,
      commit_sha: activity.commit_sha ?? stored.commit_sha ?? null,
    };
  });
}
