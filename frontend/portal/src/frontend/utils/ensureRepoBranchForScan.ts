/*
 * Copyright Red Hat
 */

import type { ApmeApi } from "../api/ApmeApi";

/** Pre-checks SCM branch existence before APME register/scan. */
export async function ensureRepoBranchForScan(
  apmeApi: ApmeApi,
  repoUrl: string,
  branch: string | undefined,
): Promise<void> {
  const resolvedBranch = branch?.trim();
  if (!resolvedBranch) {
    throw new Error(
      "No default branch is configured for this repository. Update the catalog entity branch and try again.",
    );
  }
  await apmeApi.validateRepoBranch(repoUrl, resolvedBranch);
}
