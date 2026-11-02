/*
 * Copyright Red Hat
 *
 * Prefill Quality CheckOptionsForm from portal scan-target resolution (US-004).
 */

import { DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION } from "../../common/scanTargetDefaults";
import type { ApmeApi } from "../api";

/**
 * Effective ansible-core version for a new interactive scan: project override →
 * portal global (Quality settings) → app-config → hardcoded default.
 */
export async function resolveDefaultAnsibleVersionForScan(
  apmeApi: Pick<ApmeApi, "getProjectScanTarget" | "getPortalSettings">,
  projectId: string,
): Promise<string> {
  try {
    const target = await apmeApi.getProjectScanTarget(projectId);
    const effective = target.effective?.trim();
    if (effective) {
      return effective;
    }
  } catch {
    // Fall through to portal settings / default.
  }

  try {
    const settings = await apmeApi.getPortalSettings();
    const global = settings.targetAnsibleCoreVersion?.trim();
    if (global) {
      return global;
    }
  } catch {
    // Fall through to hardcoded default.
  }

  return DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION;
}
