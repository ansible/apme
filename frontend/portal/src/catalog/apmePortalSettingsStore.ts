/*
 * Copyright Red Hat
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { ApmePortalSettingsData } from "../common/index";

const DEFAULT_RELATIVE_PATH = path.join(".data", "apme-portal-settings.json");

/** Retain only boolean enableAi; coerce invalid persisted values to false. */
function coercePortalEnableAi(value: unknown): boolean | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (typeof value === "boolean") {
    return value;
  }
  return false;
}

export class ApmePortalSettingsStore {
  private readonly filePath: string;
  private cache: ApmePortalSettingsData | null = null;

  constructor(filePath?: string) {
    this.filePath =
      filePath ??
      process.env.APME_PORTAL_SETTINGS_PATH ??
      path.join(process.cwd(), DEFAULT_RELATIVE_PATH);
  }

  /** Absolute path used for persistence (for logging / diagnostics). */
  get path(): string {
    return this.filePath;
  }

  async read(): Promise<ApmePortalSettingsData> {
    if (this.cache) {
      return this.clone(this.cache);
    }
    try {
      const raw = await fs.readFile(this.filePath, "utf8");
      // Empty or whitespace-only files (e.g. accidental touch) are treated as unset.
      if (!raw.trim()) {
        this.cache = {};
        return {};
      }
      const parsed = JSON.parse(raw) as ApmePortalSettingsData;
      this.cache = this.normalize(parsed);
      return this.clone(this.cache);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        this.cache = {};
        return {};
      }
      // Corrupt JSON should not take down remediate / settings APIs.
      if (error instanceof SyntaxError) {
        this.cache = {};
        return {};
      }
      throw error;
    }
  }

  async write(data: ApmePortalSettingsData): Promise<ApmePortalSettingsData> {
    const normalized = this.normalize(data);
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(
      this.filePath,
      `${JSON.stringify(normalized, null, 2)}\n`,
    );
    this.cache = normalized;
    return this.clone(normalized);
  }

  async updateGlobal(targetAnsibleCoreVersion: string): Promise<void> {
    const current = await this.read();
    await this.write({
      ...current,
      global: {
        ...current.global,
        targetAnsibleCoreVersion,
      },
    });
  }

  async updateGlobalSettings(updates: {
    targetAnsibleCoreVersion?: string;
    defaultAiModelId?: string | null;
    enableAi?: boolean;
    gatewayBaseUrl?: string | null;
  }): Promise<void> {
    const current = await this.read();
    const global: typeof current.global = current.global
      ? { ...current.global }
      : {};

    if (updates.targetAnsibleCoreVersion !== undefined) {
      global.targetAnsibleCoreVersion = updates.targetAnsibleCoreVersion;
    }
    if (updates.defaultAiModelId !== undefined) {
      const trimmed = updates.defaultAiModelId?.trim();
      if (trimmed) {
        global.defaultAiModelId = trimmed;
      } else {
        delete global.defaultAiModelId;
      }
    }
    if (updates.enableAi !== undefined) {
      global.enableAi = updates.enableAi;
    }
    if (updates.gatewayBaseUrl !== undefined) {
      const trimmed = updates.gatewayBaseUrl?.trim();
      if (trimmed) {
        global.gatewayBaseUrl = trimmed;
      } else {
        delete global.gatewayBaseUrl;
      }
    }

    await this.write({
      ...current,
      global,
    });
  }

  async updateProjectTarget(
    projectId: string,
    targetAnsibleCoreVersion: string | null,
  ): Promise<void> {
    const current = await this.read();
    const projects: typeof current.projects = current.projects
      ? { ...current.projects }
      : {};

    if (targetAnsibleCoreVersion === null) {
      delete projects[projectId];
    } else {
      projects[projectId] = { targetAnsibleCoreVersion };
    }

    await this.write({
      ...current,
      projects,
    });
  }

  async updateActivityOutcome(
    scanId: string,
    outcome: {
      branch_name?: string;
      pr_url?: string | null;
      commit_sha?: string | null;
    },
  ): Promise<void> {
    const current = await this.read();
    const activities: typeof current.activities = current.activities
      ? { ...current.activities }
      : {};
    activities[scanId] = {
      ...activities[scanId],
      ...(outcome.branch_name ? { branch_name: outcome.branch_name } : {}),
      ...(outcome.pr_url !== undefined ? { pr_url: outcome.pr_url } : {}),
      ...(outcome.commit_sha !== undefined
        ? { commit_sha: outcome.commit_sha }
        : {}),
    };
    await this.write({
      ...current,
      activities,
    });
  }

  /** Test helper — reset in-memory cache between cases. */
  clearCache(): void {
    this.cache = null;
  }

  /** Sanitize persisted portal settings before cache or disk write. */
  private normalize(data: ApmePortalSettingsData): ApmePortalSettingsData {
    let global: ApmePortalSettingsData["global"];
    if (data.global) {
      global = { ...data.global };
      if ("enableAi" in global) {
        global.enableAi = coercePortalEnableAi(global.enableAi);
      }
    }

    return {
      global,
      projects: data.projects ? { ...data.projects } : undefined,
      activities: data.activities ? { ...data.activities } : undefined,
    };
  }

  private clone(data: ApmePortalSettingsData): ApmePortalSettingsData {
    return structuredClone(data);
  }
}
