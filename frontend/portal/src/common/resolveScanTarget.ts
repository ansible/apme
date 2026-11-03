/*
 * Copyright Red Hat
 */

import {
  isAllowedAnsibleCoreVersion,
  normalizeAnsibleCoreVersion,
} from "./ansibleCoreVersionOptions";
import { DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION } from "./scanTargetDefaults";

export type ScanTargetSource = "project" | "global" | "config" | "default";

export interface ApmePortalSettingsData {
  global?: {
    targetAnsibleCoreVersion?: string;
    /** Abbenay chat model id (provider/model) for remediate + escalate-ai. */
    defaultAiModelId?: string;
    /** Portal AI gate; persisted in Quality settings. */
    enableAi?: boolean;
    /** Override for `ansible.apme.baseUrl` (APME Gateway). */
    gatewayBaseUrl?: string;
  };
  projects?: Record<string, { targetAnsibleCoreVersion?: string }>;
  activities?: Record<
    string,
    { branch_name?: string; pr_url?: string | null; commit_sha?: string | null }
  >;
}

export interface ActivityPortalOutcome {
  branch_name?: string;
  pr_url?: string | null;
  commit_sha?: string | null;
}

export interface ProjectScanTargetResolution {
  effective: string;
  source: ScanTargetSource;
  globalDefault: string;
  projectOverride?: string;
}

export interface ResolveScanTargetInput {
  projectId?: string;
  store?: ApmePortalSettingsData;
  configTargetAnsibleCoreVersion?: string;
}

function resolveGlobalDefault(input: ResolveScanTargetInput): {
  version: string;
  source: Exclude<ScanTargetSource, "project">;
} {
  const fromStore = normalizeAnsibleCoreVersion(
    input.store?.global?.targetAnsibleCoreVersion,
  );
  if (fromStore && isAllowedAnsibleCoreVersion(fromStore)) {
    return { version: fromStore, source: "global" };
  }

  const fromConfig = normalizeAnsibleCoreVersion(
    input.configTargetAnsibleCoreVersion,
  );
  if (fromConfig && isAllowedAnsibleCoreVersion(fromConfig)) {
    return { version: fromConfig, source: "config" };
  }

  return {
    version: DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION,
    source: "default",
  };
}

/** Resolve effective ansible-core scan target for a project (or portal default). */
export function resolveScanTarget(
  input: ResolveScanTargetInput,
): ProjectScanTargetResolution {
  const global = resolveGlobalDefault(input);

  if (input.projectId) {
    const projectOverride = normalizeAnsibleCoreVersion(
      input.store?.projects?.[input.projectId]?.targetAnsibleCoreVersion,
    );
    if (projectOverride && isAllowedAnsibleCoreVersion(projectOverride)) {
      return {
        effective: projectOverride,
        source: "project",
        globalDefault: global.version,
        projectOverride,
      };
    }
  }

  return {
    effective: global.version,
    source: global.source,
    globalDefault: global.version,
  };
}

/** Effective version string for gateway `options.ansible_version`. */
export function resolveScanTargetVersion(
  input: ResolveScanTargetInput,
): string {
  return resolveScanTarget(input).effective;
}
