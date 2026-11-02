/**
 * Portal SDK extension registrations for the APME plugin.
 *
 * ## Migration from ADR-010 to portal-extension-api (ANSTRAT-2497)
 *
 * ### Before (ADR-010)
 * APME implemented `GitRepositoriesExtensionsApi` and registered a Backstage
 * API factory via `createGitRepositoriesExtensionsApiFactory()`. self-service
 * called `extensionsApi.getDetailTabs()`, `getDetailOverviewSlots()`, etc.
 * at render time to discover what APME contributed.
 *
 * ```typescript
 * // Old: contribution was imperative + DI-based
 * createGitRepositoriesExtensionsApiFactory(ApmeGitRepositoriesExtensionsApi)
 * ```
 *
 * ### After (portal-extension-api)
 * APME calls `registerManifest(apmeManifest)` (declaring WHAT it contributes)
 * and `register*()` helpers below (providing the actual React components).
 * The host picks up contributions from the registry automatically at render time
 * via `useExtensionTabs()`, `useExtensionCards()`, `useExtensionActions()`.
 *
 * ```typescript
 * // New: contribution is declarative + registry-based
 * registerManifest(apmeManifest);      // metadata — what we contribute
 * registerApmeExtensions();            // components — how we render it
 * ```
 *
 * ### What is migrated here
 * | Surface                       | Old system          | New system              |
 * |-------------------------------|---------------------|-------------------------|
 * | Quality entity tab            | `getDetailTabs()`   | `registerGitRepoDetailTab()` |
 * | Quality Activity entity tab   | `getDetailTabs()`   | `registerGitRepoDetailTab()` |
 * | Dependencies entity tab       | `getDetailTabs()`   | `registerGitRepoDetailTab()` |
 * | Quality overview card         | `getDetailOverviewSlots()` | `registerGitRepoDetailCard()` |
 * | Run scan / deregister actions | `getDetailHeaderMenuItems()` | `registerGitRepoDetailAction()` |
 * | Add repository (list header) | `getListHeaderActions()`     | `registerGitRepoListAction()`   |
 *
 * ### What still uses ADR-010 (pending Phase 6 slot activation)
 * | Surface                       | Reason not migrated yet                        |
 * |-------------------------------|------------------------------------------------|
 * | Fleet Quality list tab        | Needs `repositoryDetailPath` routing context   |
 * | Quality Settings list tab     | Needs routing context + resource permission    |
 * | Violations catalog column     | No catalog-column extension point in SDK yet   |
 * | Catalog overlays              | No catalog-overlay extension point in SDK yet  |
 */

import { lazy } from "react";
import {
  apmeQualityScanPermission,
  apmeRepositoryDeregisterPermission,
  apmeRepositoryRegisterPermission,
} from "../../common/operations";
import type { Entity } from "@backstage/catalog-model";
import { EntityProvider } from "@backstage/plugin-catalog-react";
import {
  registerGitRepoDetailTab,
  registerGitRepoDetailCard,
  registerGitRepoDetailAction,
  registerGitRepoListAction,
  registerScaffolderField,
  CONTENT_TYPES,
} from "@ansible/portal-extension-api";
import { normalizeRepoUrlFromEntity } from "@ansible/backstage-rhaap-common/catalogEntity";
import { GitHubRepoUrlFieldExtension } from "../components/Scaffolder/GitHubRepoUrlField/GitHubRepoUrlFieldExtension";
import { GitHubRepoUrlFieldFieldSchema } from "../components/Scaffolder/GitHubRepoUrlField/schema";
import { githubRepoUrlValidation } from "../components/Scaffolder/GitHubRepoUrlField/validation";

// ── Lazy component imports ────────────────────────────────────────────────────
// Each import is lazy so APME's bundle only loads when the user navigates to a
// page that renders one of these contributions. The host wraps each in Suspense.

const EntityQualityTab = lazy(async () => {
  const { EntityQualityTab: C } =
    await import("../components/EntityQualityTab/EntityQualityTab");
  return { default: C };
});

const ApmeQualityActivityTab = lazy(async () => {
  const { ApmeQualityActivityTab: C } =
    await import("../components/ApmeQualityActivityTab/ApmeQualityActivityTab");
  return { default: C };
});

const DependenciesTab = lazy(async () => {
  const { DependenciesTab: C } =
    await import("../components/DependenciesTab/DependenciesTab");
  return { default: C };
});

const ApmeRepositoryOverviewCard = lazy(async () => {
  const { ApmeRepositoryOverviewCard: C } =
    await import("../components/ApmeRepositoryOverviewCard/ApmeRepositoryOverviewCard");
  return { default: C };
});

// ── Adapter helpers ───────────────────────────────────────────────────────────
// The portal SDK passes `entity` to contributed components.
// APME components expect a richer `GitRepositoryDetailTabContext`.
// These wrappers reconstruct the context from `entity` alone.

/**
 * Builds the `GitRepositoryDetailTabContext` the APME components need from a
 * Backstage `Entity`. `repoUrl` is derived from catalog annotations via the
 * same normalization logic self-service uses.
 */
function buildTabContext(entity: Entity) {
  return {
    entity,
    repoUrl: normalizeRepoUrlFromEntity(entity),
  };
}

// ── Registration ──────────────────────────────────────────────────────────────

/**
 * Registers all APME contributions that can be migrated to the portal SDK
 * today. Called once at plugin load time from `plugin.ts`.
 *
 * Idempotent: the registry ignores duplicate IDs registered after the first
 * call, so calling this function more than once is safe.
 */
export function registerApmeExtensions(): void {
  registerScaffolderField({
    id: "apme.github-repo-url-field",
    pluginId: "apme",
    enabledByConfig: "ansible.apme.enabled",
    field: {
      name: "GitHubRepoUrlField",
      component: GitHubRepoUrlFieldExtension,
      schema: GitHubRepoUrlFieldFieldSchema,
      validation: githubRepoUrlValidation,
    },
  });
  // ── Entity detail tabs ──────────────────────────────────────────────────

  registerGitRepoDetailTab({
    id: "apme.quality-tab",
    label: "Quality",
    priority: 10,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    component: ({ entity }: { entity?: Entity }) => {
      if (!entity) return null;
      const { initialRuleFilter, initialCategoryFilter } = getUrlFilterParams();
      return (
        <EntityQualityTab
          entity={entity}
          initialRuleFilter={initialRuleFilter}
          initialCategoryFilter={initialCategoryFilter}
        />
      );
    },
  });

  registerGitRepoDetailTab({
    id: "apme.quality-activity-tab",
    label: "Quality activity",
    priority: 15,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    component: ({ entity }: { entity?: Entity }) => {
      // ApmeQualityActivityTab uses useEntity() internally.
      // Wrap in EntityProvider so it can access the entity regardless
      // of whether the portal's ExtensionRenderer provides one.
      if (!entity) return null;
      return (
        <EntityProvider entity={entity}>
          <ApmeQualityActivityTab />
        </EntityProvider>
      );
    },
  });

  registerGitRepoDetailTab({
    id: "apme.dependencies-tab",
    label: "Dependencies",
    priority: 16,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    component: ({ entity }: { entity?: Entity }) => {
      if (!entity) return null;
      return <DependenciesTab context={buildTabContext(entity)} />;
    },
  });

  // ── Entity overview card ────────────────────────────────────────────────

  registerGitRepoDetailCard({
    id: "apme.quality-overview-card",
    slot: "sidebar",
    priority: 10,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    component: ({ entity }: { entity?: Entity }) => {
      if (!entity) return null;
      return <ApmeRepositoryOverviewCard context={buildTabContext(entity)} />;
    },
  });

  // ── Entity detail actions ────────────────────────────────────────────────
  // The host owns confirmation and dispatch; APME owns the lifecycle handler.

  registerGitRepoDetailAction({
    id: "apme.deregister-action",
    permission: apmeRepositoryDeregisterPermission,
    label: "Deregister repository",
    variant: "menu-item",
    priority: 10,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    launches: {
      type: "operation",
      operationId: "apme.repository.deregister",
    },
    confirmation: {
      title: "Remove repository?",
      message:
        "Remove this manually registered repository from the Portal catalog and stop Portal-driven tracking? Git and APME projects and scan history are preserved. Independently configured APME schedules are unchanged. Work already started may finish. You can re-register later.",
      confirmLabel: "Remove",
    },
    filter: (entity) =>
      entity.metadata.annotations?.["ansible.io/registration-method"] ===
      "manual",
  });

  registerGitRepoDetailAction({
    id: "apme.run-quality-scan-action",
    permission: apmeQualityScanPermission,
    label: "Run quality scan",
    variant: "menu-item",
    priority: 20,
    appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
    launches: {
      type: "operation",
      operationId: "apme.quality.scan",
    },
  });

  // ── List page: "Add repository" header button ─────────────────────────────
  // Renders as a contained button in the Git Repositories list page header,
  // navigating users to the scaffolder template that registers a repo with APME.
  registerGitRepoListAction({
    id: "apme.register-repository",
    permission: apmeRepositoryRegisterPermission,
    label: "Add repository",
    to: "/self-service/create/templates/default/apme-register-git-repository",
    priority: 10,
  });
}

// ── URL filter params ─────────────────────────────────────────────────────────
// `EntityQualityTab` supports deep-linked rule/category filters via URL search
// params. Read them here so the wrapper can pass them in.

function getUrlFilterParams(): {
  initialRuleFilter?: string;
  initialCategoryFilter?: string;
} {
  try {
    const params = new URLSearchParams(window.location.search);
    return {
      initialRuleFilter: params.get("rule") ?? undefined,
      initialCategoryFilter: params.get("category") ?? undefined,
    };
  } catch {
    return {};
  }
}
