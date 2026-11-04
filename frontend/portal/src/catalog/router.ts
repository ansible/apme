/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { createPortalSettingsService } from "./settingsService";
import { assertSafeAbbenayProviderId, assertSafeHttpUrl } from "./urlSafety";
import { listApmeInferenceModels, resolveApmeAiStatus } from "./aiResolution";
import PromiseRouter from "express-promise-router";
import type { Router } from "express";
import type { IncomingHttpHeaders } from "node:http";
import {
  type BackstageCredentials,
  HttpAuthService,
  LoggerService,
  PermissionsService,
} from "@backstage/backend-plugin-api";
import { Config } from "@backstage/config";
import { InputError, NotAllowedError } from "@backstage/errors";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import {
  ansibleSettingsEditPermission,
  ansibleSettingsViewPermission,
} from "@ansible/backstage-rhaap-common/permissions";
import type {
  ProjectOperationAuthorizer,
  RepositoryOperationAuthorizer,
} from "./projectOperationAuthorization";
import {
  IApmeService,
  getApmeConfig,
  isAllowedAnsibleCoreVersion,
  mergeActivityPortalOutcomes,
  normalizeApmeAiProviders,
  resolveScanTarget,
  resolveGatewayBaseUrl,
  enrichProjectsWithSeverityBreakdown,
  projectNeedsSeverityEnrichment,
} from "../common/index";
import { ApmePortalSettingsStore } from "./apmePortalSettingsStore";
import { validateRepoBranch } from "./branchLookup";
import { jsonBody } from "./jsonBody";
import { resolveIntegrationScmToken } from "./resolveIntegrationScmToken";
import { proxyProjectOperation } from "./gatewayOperationProxy";
import { createRequireSettingsManageMiddleware } from "./requireSettingsManage";
import { createRequireSettingsViewMiddleware } from "./requireSettingsView";
import { proxySharedData } from "./sharedDataProxy";

const GALAXY_SERVER_NAME_RE = /^[A-Za-z0-9_-]+$/;

function certificatePolicy(body: Record<string, unknown>): {
  validate_certs?: boolean | null;
} {
  if (!Object.prototype.hasOwnProperty.call(body, "validate_certs")) return {};
  if (body.validate_certs !== null && typeof body.validate_certs !== "boolean")
    throw new InputError("validate_certs must be boolean or null");
  return { validate_certs: body.validate_certs };
}

/** Headers + optional body — avoids dual `@types/express` Request mismatches. */
type ApmeHttpRequest = {
  headers: IncomingHttpHeaders;
  body?: unknown;
};

export interface RouterOptions {
  apmeService: IApmeService;
  logger: LoggerService;
  httpAuth: HttpAuthService;
  rootConfig: Config;
  portalSettingsStore?: ApmePortalSettingsStore;
  permissions: PermissionsService;
  /** Missing authorizer denies interactive workflow traffic. */
  authorizeProjectOperation?: ProjectOperationAuthorizer;
  /** Authorizes a catalog repository before SCM metadata lookup or registration. */
  authorizeRepositoryOperation?: RepositoryOperationAuthorizer;
  /** Dedicated host policy: entitlement, repository access and destructive permission.
   * Omission denies project deletion; scan authorization never grants deletion.
   */
  authorizeProjectDeletion?: (
    projectId: string,
    credentials: BackstageCredentials,
  ) => Promise<void>;
}

function scmTokenFromBody(body: unknown): string | undefined {
  if (!body || typeof body !== "object") {
    return undefined;
  }
  const raw = (body as { scm_token?: unknown }).scm_token;
  if (typeof raw !== "string") {
    return undefined;
  }
  const token = raw.trim();
  return token || undefined;
}

function scmTokenFromRequest(req: ApmeHttpRequest): string | undefined {
  const raw =
    (req.headers["x-scm-token"] as string | undefined) ??
    (req.headers["x-github-token"] as string | undefined) ??
    (req.headers["x-gitlab-token"] as string | undefined);
  const token = raw?.trim();
  return token || undefined;
}

async function ensureRepoBranchValid(
  rootConfig: Config,
  logger: LoggerService,
  repoUrl: string,
  branch: string | undefined,
  scmToken?: string,
): Promise<void> {
  const resolvedBranch = branch?.trim();
  if (!resolvedBranch) {
    throw new InputError("branch is required");
  }
  await validateRepoBranch({
    rootConfig,
    repoUrl,
    branch: resolvedBranch,
    scmToken,
    logger,
  });
}

export async function createRouter(options: RouterOptions): Promise<Router> {
  await Promise.resolve();
  const {
    apmeService,
    logger,
    httpAuth,
    rootConfig,
    portalSettingsStore = new ApmePortalSettingsStore(),
    permissions,
  } = options;
  const router = PromiseRouter();

  const configSnapshot = getApmeConfig(rootConfig);

  const requireApmeSettingsManage = createRequireSettingsManageMiddleware({
    httpAuth,
    permissions,
  });

  const requireApmeSettingsView = createRequireSettingsViewMiddleware({
    httpAuth,
    permissions,
  });

  const resolveScmTokenForProject = async (
    req: ApmeHttpRequest,
    projectId: string,
  ): Promise<string | undefined> => {
    let scmToken = scmTokenFromRequest(req) ?? scmTokenFromBody(req.body);
    if (scmToken) {
      return scmToken;
    }
    const project = await apmeService.getProject(projectId);
    scmToken = await resolveIntegrationScmToken({
      rootConfig,
      logger,
      repoUrl: project.repo_url,
    });
    if (scmToken) {
      logger.info(
        `APME using integration/GitHub App token for project ${projectId}`,
      );
    }
    return scmToken;
  };

  const settingsService = createPortalSettingsService(
    rootConfig,
    portalSettingsStore,
  );
  const mergedPortalSettings = settingsService.get;

  const resolveEffectiveGatewayBaseUrl = async () => {
    const store = await portalSettingsStore.read();
    return resolveGatewayBaseUrl({
      store,
      configBaseUrl: configSnapshot.baseUrl,
    }).effective;
  };

  // Body parsing: use route-level `jsonBody` only (see ./jsonBody.ts). Never
  // `router.use(jsonBody)` — this router shares the catalog HTTP stack.

  const ensureUser = (req: unknown) => {
    return httpAuth.credentials(
      req as Parameters<HttpAuthService["credentials"]>[0],
      {
        allow: ["user"],
      },
    );
  };
  const authorizeProject = async (
    projectId: string,
    credentials: Awaited<ReturnType<typeof ensureUser>>,
    mutating: boolean,
  ) => {
    if (!options.authorizeProjectOperation) {
      throw new NotAllowedError(
        "Project operation authorization is unavailable",
      );
    }
    await options.authorizeProjectOperation(projectId, credentials, mutating);
  };
  const authorizeWorkflow = async (req: {
    method: string;
    params: { projectId?: string };
  }) => {
    const credentials = await ensureUser(req);
    if (!req.params.projectId) throw new InputError("projectId is required");
    await authorizeProject(
      req.params.projectId,
      credentials,
      !["GET", "HEAD"].includes(req.method),
    );
    return credentials.principal.userEntityRef;
  };
  const authorizeRepository = async (
    req: unknown,
    repoUrl: string,
    branch: string,
    operation: "read" | "register",
  ) => {
    const credentials = await ensureUser(req);
    if (!options.authorizeRepositoryOperation) {
      throw new NotAllowedError(
        "Repository operation authorization is unavailable",
      );
    }
    await options.authorizeRepositoryOperation(
      repoUrl,
      branch,
      credentials,
      operation,
    );
  };
  const authorizeSettingsPermission = async (
    req: unknown,
    permission:
      | typeof ansibleSettingsEditPermission
      | typeof ansibleSettingsViewPermission,
  ) => {
    const credentials = await ensureUser(req);
    const [decision] = await permissions.authorize(
      [{ permission, resourceRef: "apme" }],
      { credentials },
    );
    if (decision?.result !== AuthorizeResult.ALLOW) {
      throw new NotAllowedError("APME settings permission denied");
    }
  };

  const outboundUrlOptions = () => ({
    allowHttp:
      rootConfig.getOptionalBoolean("ansible.apme.allowHttpOutboundUrls") ??
      false,
    allowPrivateHosts:
      rootConfig.getOptionalBoolean("ansible.apme.allowPrivateOutboundUrls") ??
      false,
  });

  // Lossless shared-package data routes. Existing Portal envelopes remain stable.
  router.get("/apme/shared/rules", async (req, res) => {
    await ensureUser(req);
    await proxySharedData({
      gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
      path: "/rules",
      response: res,
    });
  });
  router.get(
    "/apme/shared/ai/config",
    requireApmeSettingsView,
    async (_req, res) => {
      await proxySharedData({
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
        path: "/ai/config",
        response: res,
      });
    },
  );
  router.get(
    "/apme/shared/ai/providers",
    requireApmeSettingsView,
    async (_req, res) => {
      await proxySharedData({
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
        path: "/ai/providers",
        response: res,
      });
    },
  );
  router.get(
    "/apme/shared/ai/engines",
    requireApmeSettingsView,
    async (_req, res) => {
      await proxySharedData({
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
        path: "/ai/engines",
        response: res,
      });
    },
  );
  const sharedProjectData =
    (suffix: string) =>
    async (
      req: {
        method: string;
        params: { projectId: string };
        query: Record<string, unknown>;
      },
      res: Parameters<typeof proxySharedData>[0]["response"],
    ) => {
      await authorizeWorkflow(req);
      const params = new URLSearchParams();
      for (const key of [
        "limit",
        "offset",
        "severity",
        "rule_id",
        "include_acknowledged",
        "sort_by",
        "order",
      ]) {
        const value = req.query[key];
        if (typeof value === "string") params.set(key, value);
      }
      await proxySharedData({
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
        path: `/projects/${encodeURIComponent(req.params.projectId)}${suffix}`,
        query: params.size ? `?${params}` : "",
        response: res,
      });
    };
  router.get("/apme/shared/projects/:projectId", sharedProjectData(""));
  router.get(
    "/apme/shared/projects/:projectId/activity",
    sharedProjectData("/activity"),
  );
  router.get(
    "/apme/shared/projects/:projectId/violations",
    sharedProjectData("/violations"),
  );
  router.get(
    "/apme/shared/projects/:projectId/dependencies",
    sharedProjectData("/dependencies"),
  );
  router.get(
    "/apme/shared/projects/:projectId/graph",
    sharedProjectData("/graph"),
  );
  router.get(
    "/apme/shared/projects/:projectId/sbom",
    sharedProjectData("/sbom"),
  );
  router.get(
    "/apme/shared/projects/:projectId/activity/:scanId",
    async (req, res) => {
      await authorizeWorkflow(req);
      await proxySharedData({
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
        path: `/activity/${encodeURIComponent(req.params.scanId)}`,
        response: res,
        verifyProjectId: req.params.projectId,
      });
    },
  );

  router.get("/apme/health", async (req, res) => {
    await ensureUser(req);
    logger.debug("APME health check requested");
    const health = await apmeService.getHealth();
    res.json(health);
  });

  router.get("/apme/settings", requireApmeSettingsView, async (_req, res) => {
    res.json(await mergedPortalSettings());
  });

  router.put(
    "/apme/settings",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      res.json(await settingsService.save(req.body ?? {}));
    },
  );

  router.get(
    "/apme/settings/galaxy-servers",
    requireApmeSettingsView,
    async (_req, res) => {
      const servers = await apmeService.listGalaxyServers();
      res.json(servers);
    },
  );

  router.post(
    "/apme/settings/galaxy-servers",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      const { name, url, token, auth_url } = req.body ?? {};
      if (typeof name !== "string" || !name.trim()) {
        throw new InputError("name is required");
      }
      if (!GALAXY_SERVER_NAME_RE.test(name.trim())) {
        throw new InputError("name must match [A-Za-z0-9_-]+");
      }
      if (typeof url !== "string" || !url.trim()) {
        throw new InputError("url is required");
      }
      await assertSafeHttpUrl(url, "url", outboundUrlOptions());
      if (typeof auth_url === "string" && auth_url.trim()) {
        await assertSafeHttpUrl(auth_url, "auth_url", outboundUrlOptions());
      }
      const server = await apmeService.createGalaxyServer({
        ...certificatePolicy(req.body ?? {}),
        name: name.trim(),
        url: url.trim(),
        token: typeof token === "string" ? token : undefined,
        auth_url: typeof auth_url === "string" ? auth_url : undefined,
      });
      res.status(201).json(server);
    },
  );

  router.patch(
    "/apme/settings/galaxy-servers/:serverId",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      const serverId = Number.parseInt(req.params.serverId, 10);
      if (!Number.isFinite(serverId)) {
        throw new InputError("serverId must be a number");
      }
      const { name, url, token, auth_url } = req.body ?? {};
      const body: {
        name?: string;
        url?: string;
        token?: string;
        auth_url?: string;
        validate_certs?: boolean | null;
      } = { ...certificatePolicy(req.body ?? {}) };
      if (typeof name === "string") {
        if (!GALAXY_SERVER_NAME_RE.test(name.trim())) {
          throw new InputError("name must match [A-Za-z0-9_-]+");
        }
        body.name = name.trim();
      }
      if (typeof url === "string") {
        await assertSafeHttpUrl(url, "url", outboundUrlOptions());
        body.url = url.trim();
      }
      if (typeof token === "string") body.token = token;
      if (typeof auth_url === "string") {
        if (auth_url.trim()) {
          await assertSafeHttpUrl(auth_url, "auth_url", outboundUrlOptions());
        }
        body.auth_url = auth_url;
      }
      if (Object.keys(body).length === 0) {
        throw new InputError("At least one field is required");
      }
      const server = await apmeService.updateGalaxyServer(serverId, body);
      res.json(server);
    },
  );

  router.delete(
    "/apme/settings/galaxy-servers/:serverId",
    requireApmeSettingsManage,
    async (req, res) => {
      const serverId = Number.parseInt(req.params.serverId, 10);
      if (!Number.isFinite(serverId)) {
        throw new InputError("serverId must be a number");
      }
      await apmeService.deleteGalaxyServer(serverId);
      res.status(204).send();
    },
  );

  router.get("/apme/projects/:projectId/scan-target", async (req, res) => {
    await authorizeWorkflow(req);
    const { projectId } = req.params;
    const store = await portalSettingsStore.read();
    res.json(
      resolveScanTarget({
        projectId,
        store,
        configTargetAnsibleCoreVersion: configSnapshot.targetAnsibleCoreVersion,
      }),
    );
  });

  router.put(
    "/apme/projects/:projectId/scan-target",
    jsonBody,
    async (req, res) => {
      await authorizeWorkflow(req);
      const { projectId } = req.params;
      const { targetAnsibleCoreVersion } = req.body ?? {};
      if (
        targetAnsibleCoreVersion !== null &&
        (typeof targetAnsibleCoreVersion !== "string" ||
          !targetAnsibleCoreVersion.trim())
      ) {
        throw new InputError(
          "targetAnsibleCoreVersion must be a version string or null",
        );
      }
      if (
        typeof targetAnsibleCoreVersion === "string" &&
        !isAllowedAnsibleCoreVersion(targetAnsibleCoreVersion)
      ) {
        throw new InputError(
          `Unsupported ansible-core version: ${targetAnsibleCoreVersion}`,
        );
      }
      await portalSettingsStore.updateProjectTarget(
        projectId,
        targetAnsibleCoreVersion === null
          ? null
          : targetAnsibleCoreVersion.trim(),
      );
      const store = await portalSettingsStore.read();
      res.json(
        resolveScanTarget({
          projectId,
          store,
          configTargetAnsibleCoreVersion:
            configSnapshot.targetAnsibleCoreVersion,
        }),
      );
    },
  );

  router.get("/apme/ai/status", async (req, res) => {
    await ensureUser(req);
    const settings = await mergedPortalSettings();
    const status = await resolveApmeAiStatus(apmeService, logger);
    res.json({
      enableAi: settings.enableAi,
      connected: status.connected,
      modelCount: status.modelCount,
      configuredModelCount: status.configuredModelCount,
    });
  });

  // @apme/ui-workflow CheckOptionsForm → GET /ai/models (via catalog proxy apiBase).
  // Prefers live inference; falls back to config models (AAP-89202).
  router.get("/apme/ai/models", async (req, res) => {
    await ensureUser(req);
    const models = await listApmeInferenceModels(apmeService);
    res.json(models);
  });

  // US-016: Abbenay AI provider CRUD (portal → Gateway ADR-070 → Abbenay).
  router.get("/apme/ai/config", requireApmeSettingsView, async (_req, res) => {
    const config = await apmeService.getAiConfig();
    res.json(config);
  });

  router.post(
    "/apme/ai/config",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      const result = await apmeService.updateAiConfig(req.body);
      res.json(result);
    },
  );

  router.get("/apme/ai/providers", async (req, res) => {
    await ensureUser(req);
    // Abbenay's provider registry (GET /api/providers) never includes model
    // IDs. See ProviderInfo in abbenay/packages/daemon/src/core/state.ts.
    // `models` is always [] here; callers needing real model IDs must merge
    // with GET /apme/ai/config via mergeApmeAiProviderLists (see
    // ApmeAiProvidersSection.tsx).
    const raw = await apmeService.getAiProviders();
    res.json(normalizeApmeAiProviders(raw));
  });

  router.get("/apme/ai/engines", requireApmeSettingsView, async (_req, res) => {
    const result = await apmeService.getAiEngines();
    res.json(result);
  });

  router.post(
    "/apme/ai/provider/:id/configure",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      const { id } = req.params;
      if (!id) {
        throw new InputError("Provider id is required");
      }
      assertSafeAbbenayProviderId(id);
      const result = await apmeService.configureAiProvider(id, req.body);
      res.json(result);
    },
  );

  router.delete(
    "/apme/ai/provider/:id",
    requireApmeSettingsManage,
    async (req, res) => {
      const { id } = req.params;
      if (id) {
        assertSafeAbbenayProviderId(id);
      }
      await apmeService.deleteAiProvider(id);
      res.status(204).send();
    },
  );

  router.get("/apme/projects", async (req, res) => {
    const credentials = await ensureUser(req);
    logger.debug("APME projects list requested");
    const projects = await apmeService.getProjects();
    const authorizedProjects = await Promise.all(
      projects.map(async (project) => {
        try {
          await authorizeProject(project.id, credentials, false);
          return project;
        } catch (error) {
          if (error instanceof NotAllowedError) return undefined;
          throw error;
        }
      }),
    );
    const visibleProjects = authorizedProjects.filter(
      (project): project is (typeof projects)[number] => project !== undefined,
    );
    const enrichmentCount = visibleProjects.filter(
      projectNeedsSeverityEnrichment,
    ).length;
    if (enrichmentCount > 0) {
      logger.info(
        `Enriching severity breakdown for ${enrichmentCount} of ${visibleProjects.length} accessible APME projects`,
      );
    }
    const items = await enrichProjectsWithSeverityBreakdown(
      visibleProjects,
      (projectId) => apmeService.getProject(projectId),
    );
    res.json({ items });
  });

  router.get("/apme/projects/:projectId", async (req, res) => {
    const { projectId } = req.params;
    await authorizeWorkflow(req);
    logger.debug(`APME project ${projectId} requested`);
    const project = await apmeService.getProject(projectId);
    res.json(project);
  });

  router.get("/apme/projects/:projectId/violations", async (req, res) => {
    const { projectId } = req.params;
    await authorizeWorkflow(req);
    logger.debug(`APME violations for project ${projectId} requested`);
    const limitRaw = req.query.limit;
    const offsetRaw = req.query.offset;
    const limit =
      typeof limitRaw === "string" ? Number.parseInt(limitRaw, 10) : undefined;
    const offset =
      typeof offsetRaw === "string"
        ? Number.parseInt(offsetRaw, 10)
        : undefined;
    const violationOptions =
      (limit !== undefined && !Number.isNaN(limit)) ||
      (offset !== undefined && !Number.isNaN(offset))
        ? {
            ...(limit !== undefined && !Number.isNaN(limit) ? { limit } : {}),
            ...(offset !== undefined && !Number.isNaN(offset)
              ? { offset }
              : {}),
          }
        : undefined;
    const violations = await apmeService.getViolations(
      projectId,
      violationOptions,
    );
    res.json(violations);
  });

  router.get("/apme/projects/:projectId/dependencies", async (req, res) => {
    const { projectId } = req.params;
    await authorizeWorkflow(req);
    logger.debug(`APME dependencies for project ${projectId} requested`);
    const dependencies = await apmeService.getProjectDependencies(projectId);
    res.json(dependencies);
  });

  // Transparent Gateway proxy for @apme/ui-workflow operation surface
  // (GET/POST/PATCH/SSE under /operation*). ADR-056: no file_overrides.
  // Skip jsonBody on GET/HEAD — SSE /operation/events must not hit the
  // body parser (keeps the streaming path lean).
  const jsonBodyUnlessGet: typeof jsonBody = (req, res, next) => {
    if (req.method === "GET" || req.method === "HEAD") {
      next();
      return;
    }
    jsonBody(req, res, next);
  };

  router.all(
    "/apme/projects/:projectId/operation",
    jsonBodyUnlessGet,
    async (req, res) => {
      const userEntityRef = await authorizeWorkflow(req);
      const { projectId } = req.params;
      const scmToken = await resolveScmTokenForProject(req, projectId);
      await proxyProjectOperation({
        req,
        res,
        rootConfig,
        logger,
        projectId,
        operationSuffix: "",
        scmToken,
        userEntityRef,
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
      });
    },
  );

  router.all(
    /^\/apme\/projects\/([^/]+)\/operation\/(.+)$/,
    jsonBodyUnlessGet,
    async (req, res) => {
      // RegExp routes work with both host Express 5 and plugin Express 4.
      // Normalize the positional capture before the existing project authorizer.
      const projectId = req.params[0];
      req.params.projectId = projectId;
      const userEntityRef = await authorizeWorkflow(req);
      const scmToken = await resolveScmTokenForProject(req, projectId);
      const splat = req.params[1];
      const rest = splat ? `/${splat}` : "";
      await proxyProjectOperation({
        req,
        res,
        rootConfig,
        logger,
        projectId,
        operationSuffix: rest,
        scmToken,
        userEntityRef,
        gatewayBaseUrl: await resolveEffectiveGatewayBaseUrl(),
      });
    },
  );

  router.get("/apme/rules", async (req, res) => {
    await ensureUser(req);
    logger.debug("APME rules list requested");
    const rules = await apmeService.getRules();
    res.json({ items: rules });
  });

  router.put(
    "/apme/rules/:ruleId/config",
    jsonBody,
    requireApmeSettingsManage,
    async (req, res) => {
      const { ruleId } = req.params;
      const body = req.body;
      if (!body || typeof body !== "object") {
        throw new InputError("Request body must be an object");
      }
      const hasValidField =
        "severity_override" in body ||
        "enabled_override" in body ||
        "enforced" in body;
      if (!hasValidField) {
        throw new InputError(
          "At least one of severity_override, enabled_override, or enforced is required",
        );
      }
      logger.info(`APME rule config update for ${ruleId}`);
      const rule = await apmeService.updateRuleConfig(ruleId, body);
      res.json(rule);
    },
  );

  router.delete(
    "/apme/rules/:ruleId/config",
    requireApmeSettingsManage,
    async (req, res) => {
      const { ruleId } = req.params;
      logger.info(`APME rule config reset for ${ruleId}`);
      await apmeService.deleteRuleConfig(ruleId);
      res.status(204).send();
    },
  );

  router.post("/apme/suppressions", jsonBody, async (req, res) => {
    const credentials = await ensureUser(req);
    const { rule_id, scope } = req.body ?? {};
    if (!rule_id || typeof rule_id !== "string") {
      throw new InputError("rule_id is required in request body");
    }
    if (!scope || typeof scope !== "string") {
      throw new InputError("scope is required in request body");
    }
    const projectScope = /^project:(.+)$/.exec(scope);
    if (projectScope) {
      await authorizeProject(projectScope[1], credentials, true);
    } else {
      await authorizeSettingsPermission(req, ansibleSettingsEditPermission);
    }
    logger.info("APME suppression create requested");
    const suppression = await apmeService.createSuppression(req.body);
    res.status(201).json(suppression);
  });

  router.get("/apme/suppressions", async (req, res) => {
    const scope = req.query.scope as string | undefined;
    const projectScope = scope && /^project:(.+)$/.exec(scope);
    if (projectScope) {
      const credentials = await ensureUser(req);
      await authorizeProject(projectScope[1], credentials, false);
    } else {
      await authorizeSettingsPermission(req, ansibleSettingsViewPermission);
    }
    logger.debug(`APME suppressions list requested scope=${scope ?? "all"}`);
    const suppressions = await apmeService.getSuppressions(scope);
    res.json(suppressions);
  });

  router.delete("/apme/suppressions/:suppressionId", async (req, res) => {
    const credentials = await ensureUser(req);
    const suppressionId = Number(req.params.suppressionId);
    if (!Number.isSafeInteger(suppressionId) || suppressionId < 0) {
      res.status(400).json({ error: "Invalid suppression id" });
      return;
    }
    const suppressions = await apmeService.getSuppressions();
    const suppression = Array.isArray(suppressions)
      ? suppressions.find(
          (item) => (item as { id?: number }).id === suppressionId,
        )
      : undefined;
    if (!suppression) {
      res.status(404).json({ error: "Suppression not found" });
      return;
    }
    const scope = (suppression as { scope?: unknown }).scope;
    const projectScope =
      typeof scope === "string" && /^project:(.+)$/.exec(scope);
    if (projectScope) {
      await authorizeProject(projectScope[1], credentials, true);
    } else {
      await authorizeSettingsPermission(req, ansibleSettingsEditPermission);
    }
    logger.info(`APME suppression delete ${suppressionId}`);
    await apmeService.deleteSuppression(suppressionId);
    res.status(204).send();
  });

  router.get("/apme/lookup", async (req, res) => {
    const credentials = await ensureUser(req);
    const repoUrl = req.query.repo_url as string;
    const branch = req.query.branch as string | undefined;
    if (!repoUrl) {
      res.status(400).json({ error: "repo_url query parameter is required" });
      return;
    }
    logger.debug(
      `APME project lookup by repo URL: ${repoUrl}${
        branch ? ` branch=${branch}` : ""
      }`,
    );
    const project = await apmeService.getProjectByRepoUrl(repoUrl, branch);
    if (!project) {
      res.status(404).json({ error: "Project not found" });
      return;
    }
    try {
      await authorizeProject(project.id, credentials, false);
    } catch (error) {
      if (error instanceof NotAllowedError) {
        res.status(404).json({ error: "Project not found" });
        return;
      }
      throw error;
    }
    res.json(project);
  });

  router.get("/apme/repos/branch-check", async (req, res) => {
    const repoUrl = req.query.repo_url;
    const branch = req.query.branch;
    if (typeof repoUrl !== "string" || !repoUrl.trim()) {
      throw new InputError("repo_url query parameter is required");
    }
    if (typeof branch !== "string" || !branch.trim()) {
      throw new InputError("branch query parameter is required");
    }
    await authorizeRepository(req, repoUrl, branch, "read");
    const scmToken = scmTokenFromRequest(req);
    await ensureRepoBranchValid(rootConfig, logger, repoUrl, branch, scmToken);
    res.json({ valid: true });
  });

  router.post("/apme/projects", jsonBody, async (req, res) => {
    const { name, repo_url, branch } = req.body ?? {};
    if (!name || typeof name !== "string") {
      throw new InputError("name is required in request body");
    }
    if (!repo_url || typeof repo_url !== "string") {
      throw new InputError("repo_url is required in request body");
    }
    if (typeof branch !== "string" || !branch.trim()) {
      throw new InputError("branch is required");
    }
    await authorizeRepository(req, repo_url, branch, "register");
    const scmToken = scmTokenFromRequest(req) ?? scmTokenFromBody(req.body);
    await ensureRepoBranchValid(rootConfig, logger, repo_url, branch, scmToken);
    logger.info("APME create project requested");
    const project = await apmeService.createProject(req.body);
    res.status(201).json(project);
  });

  router.delete("/apme/projects/:projectId", async (req, res) => {
    const credentials = await ensureUser(req);
    const { projectId } = req.params;
    if (!options.authorizeProjectDeletion)
      throw new NotAllowedError(
        "Project deletion authorization is unavailable",
      );
    await options.authorizeProjectDeletion(projectId, credentials);
    logger.info(`APME delete project ${projectId} requested`);
    await apmeService.deleteProject(projectId);
    res.status(204).send();
  });

  router.get("/apme/projects/:projectId/activity", async (req, res) => {
    const { projectId } = req.params;
    await authorizeWorkflow(req);
    logger.debug(`APME activity for project ${projectId} requested`);
    const activity = await apmeService.getActivity(projectId);
    const store = await portalSettingsStore.read();
    res.json(mergeActivityPortalOutcomes(activity, store.activities));
  });

  router.get("/apme/activity/:activityId", async (req, res) => {
    await ensureUser(req);
    const { activityId } = req.params;
    logger.debug(`APME activity detail ${activityId} requested`);
    const detail = await apmeService.getActivityDetail(activityId);
    if (!detail.project_id) {
      res.status(404).json({ error: "Activity not found" });
      return;
    }
    await authorizeWorkflow({
      method: "GET",
      params: { projectId: detail.project_id },
    });
    const store = await portalSettingsStore.read();
    const [merged] = mergeActivityPortalOutcomes([detail], store.activities);
    res.json(merged);
  });

  // Legacy portal path — Gateway-only submit (ADR-056). Prefer
  // POST …/operation/submit via the transparent proxy above.
  router.post(
    "/apme/projects/:projectId/submit",
    jsonBody,
    async (req, res) => {
      await authorizeWorkflow(req);
      const { projectId } = req.params;
      const {
        activity_id: activityId,
        branch_name: branchName,
        create_pr: createPr,
        title,
        body: prBody,
        file_overrides: fileOverrides,
      } = req.body as {
        activity_id?: string;
        branch_name?: string;
        create_pr?: boolean;
        title?: string;
        body?: string;
        file_overrides?: unknown;
      };

      if (fileOverrides !== undefined) {
        throw new InputError(
          "file_overrides are not supported; the APME service owns SCM commit/push (ADR-056)",
        );
      }

      if (!activityId || typeof activityId !== "string") {
        throw new InputError("activity_id is required in request body");
      }

      logger.info(
        `APME SCM submit for project ${projectId} activity ${activityId}`,
      );

      const token = await resolveScmTokenForProject(req, projectId);
      const result = await apmeService.submitRemediation(projectId, {
        activity_id: activityId,
        branch_name: branchName,
        create_pr: createPr,
        title,
        body: prBody,
        scm_token: token,
      });

      await portalSettingsStore.updateActivityOutcome(activityId, {
        branch_name: result.branch_name,
        pr_url: result.pr_url ?? null,
        commit_sha: result.commit_sha ?? null,
      });

      res.status(200).json(result);
    },
  );

  return router as unknown as Router;
}
