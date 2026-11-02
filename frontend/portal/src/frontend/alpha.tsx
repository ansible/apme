import { withPortalGate } from "./PortalContributionGate";
import type { Entity } from "@backstage/catalog-model";
import { isGitRepositoryEntity } from "./utils/isGitRepositoryEntity";
import {
  ApiBlueprint,
  PageBlueprint,
  createFrontendPlugin,
  type FrontendPlugin,
} from "@backstage/frontend-plugin-api";
import { compatWrapper } from "@backstage/core-compat-api";
import {
  EntityDetailTabBlueprint,
  EntityActionBlueprint,
  EntityOverviewCardBlueprint,
  CatalogItemActionBlueprint,
  ScaffolderFieldBlueprint,
  defineScaffolderField,
  CatalogHeaderActionBlueprint,
  ScaffolderWorkflowBlueprint,
  CatalogTabBlueprint,
  CatalogTableColumnBlueprint,
} from "@ansible/portal-extension-api/alpha";
import { apmeApiFactory } from "./api/apmeApiFactory";
import { apmeManifest } from "./apmeManifest";
import { normalizeRepoUrlFromEntity } from "@ansible/backstage-rhaap-common/catalogEntity";
import { apmeRepositoryDeregisterOperation } from "../common/operations";
import { isManuallyRegisteredRepository } from "./hooks/useDeregisterRepository";
import AssessmentIcon from "@material-ui/icons/Assessment";

const portalGates = {
  enabledByConfig: "ansible.apme.enabled",
  requiredEntitlements: ["apme.scanning"],
  appliesToContentTypes: ["playbook-repository"],
};

const lifecycleGates = { ...portalGates, requiredEntitlements: [] };
const entityGates = {
  ...portalGates,
  isApplicable: ({ entity }: { entity: Entity }) =>
    isGitRepositoryEntity(entity) &&
    normalizeRepoUrlFromEntity(entity) !== null,
};

// Native declarations reuse domain components without legacy side effects.
const capability = apmeManifest.capabilities.find(
  (item) => item.id === "apme.quality-assessment",
)!;
const qualityEntry = capability.entryPoints.find(
  (item) => item.id === "apme.quality-assessment.quality-tab",
)!;
const scanEntry = capability.entryPoints.find(
  (item) => item.id === "apme.quality-assessment.scan-action",
)!;
const overviewEntry = capability.entryPoints.find(
  (item) => item.id === "apme.quality-assessment.overview-card",
)!;
const activityEntry = capability.entryPoints.find(
  (item) => item.id === "apme.quality-assessment.quality-activity-tab",
)!;
const fleetEntry = capability.entryPoints.find(
  (item) => item.id === "apme.quality-assessment.fleet-quality-tab",
)!;
const columnEntry = capability.entryPoints.find(
  (item) => item.kind === "table-column",
)!;
const settingsCapability = apmeManifest.capabilities.find(
  (item) => item.id === "apme.quality-settings",
)!;
const settingsEntry = settingsCapability.entryPoints[0];
const dependenciesCapability = apmeManifest.capabilities.find(
  (item) => item.id === "apme.dependencies",
)!;
const dependenciesEntry = dependenciesCapability.entryPoints[0];
const lifecycleCapability = apmeManifest.capabilities.find(
  (item) => item.id === "apme.repository-lifecycle",
)!;
const addRepositoryEntry = lifecycleCapability.entryPoints.find(
  (item) => item.id === "apme.repository-lifecycle.add-repository",
)!;
const deregisterEntry = {
  id: "apme.repository-lifecycle.deregister-action",
  kind: "entity-action" as const,
  surface: "entity-page" as const,
  label: "Deregister repository",
  order: 20,
  appliesToContentTypes: ["playbook-repository"],
  requiredPermission: apmeRepositoryDeregisterOperation.permission,
  launches: {
    type: "operation" as const,
    operationId: apmeRepositoryDeregisterOperation.id,
  },
};
const catalogDeregisterEntry = {
  ...deregisterEntry,
  id: "apme.repository-lifecycle.catalog-deregister-action",
  kind: "catalog-item-action" as const,
  surface: "catalog-item" as const,
};
const removalPresentation = {
  confirmation: {
    title: "Remove repository?",
    confirmLabel: "Remove repository",
    description:
      "This removes only the repository registration from the Portal. The underlying Git repository, APME project, and scan history are preserved. Portal-driven tracking stops; independently configured APME schedules are unchanged. Work already started may finish. You can re-register later.",
  },
  completion: "subject-removed" as const,
  isApplicable: ({
    entity,
  }: {
    entity: import("@backstage/catalog-model").Entity;
  }) => isManuallyRegisteredRepository(entity),
  ...lifecycleGates,
};
const catalogScanEntry = {
  ...scanEntry,
  id: "apme.quality-assessment.catalog-scan-action",
  kind: "catalog-item-action" as const,
  surface: "catalog-item" as const,
  launches: { type: "operation" as const, operationId: "apme.quality.scan" },
};
const nfsManifest = {
  ...apmeManifest,
  capabilities: [
    {
      ...capability,
      entryPoints: [
        {
          ...qualityEntry,
          launches: { type: "slot" as const, targetSlot: "entity.detail.tabs" },
        },
        scanEntry,
        {
          ...fleetEntry,
          launches: { type: "slot" as const, targetSlot: "catalog.tabs" },
        },
        {
          ...activityEntry,
          launches: { type: "slot" as const, targetSlot: "entity.detail.tabs" },
        },
        catalogScanEntry,
        {
          ...columnEntry,
          launches: {
            type: "slot" as const,
            targetSlot: "catalog.table.columns",
          },
        },
        {
          ...overviewEntry,
          launches: {
            type: "slot" as const,
            targetSlot: "entity.overview.secondary",
          },
        },
      ],
    },
    {
      ...dependenciesCapability,
      entryPoints: [
        {
          ...dependenciesEntry,
          launches: { type: "slot" as const, targetSlot: "entity.detail.tabs" },
        },
      ],
    },
    {
      ...lifecycleCapability,
      entryPoints: [
        addRepositoryEntry,
        deregisterEntry,
        catalogDeregisterEntry,
      ],
    },
    {
      ...settingsCapability,
      entryPoints: [
        {
          ...settingsEntry,
          launches: { type: "slot" as const, targetSlot: "catalog.tabs" },
        },
      ],
    },
  ],
};

export const apmeQualityTab = EntityDetailTabBlueprint.make({
  name: "quality",
  params: {
    manifest: nfsManifest,
    capabilityId: capability.id,
    entryPointId: qualityEntry.id,
    attachment: "entity.detail.tabs",
    ...entityGates,
    requiredEntitlements: ["apme.scanning"],
    loader: async ({ entity }) => {
      const { NativeEntityQualityTab } =
        await import("./components/EntityQualityTab/NativeEntityQualityTab");
      return compatWrapper(<NativeEntityQualityTab entity={entity} />);
    },
  },
});

export const apmeScanAction = EntityActionBlueprint.make({
  name: "scan",
  params: {
    manifest: nfsManifest,
    capabilityId: capability.id,
    entryPointId: scanEntry.id,
    attachment: "entity.actions",
    ...entityGates,
    requiredEntitlements: ["apme.scanning"],
    followOnEntryPointId: qualityEntry.id,
  },
});

const apmePlugin: FrontendPlugin = createFrontendPlugin({
  pluginId: "apme",
  extensions: [
    PageBlueprint.make({
      name: "content-quality",
      params: {
        path: "/self-service/content-quality",
        title: "Content Quality",
        icon: <AssessmentIcon />,
        noHeader: true,
        loader: async () => {
          const { ContentQualityPage } =
            await import("./components/ContentQuality/ContentQualityPage");
          return compatWrapper(
            <ContentQualityPage catalogTabId={fleetEntry.id} />,
          );
        },
      },
    }),
    ApiBlueprint.make({
      name: "apme",
      params: (define) => define(apmeApiFactory),
    }),
    ScaffolderFieldBlueprint.make({
      name: "github-repository-url",
      params: defineScaffolderField({
        manifest: apmeManifest,
        capabilityId: "apme.repository-lifecycle",
        entryPointId: "apme.github-repo-url-field",
        attachment: "scaffolder.fields",
        fieldName: "GitHubRepoUrlField",
        ...lifecycleGates,
        loader: async () => {
          const [
            { GitHubRepoUrlFieldExtension },
            { GitHubRepoUrlFieldFieldSchema },
            { githubRepoUrlValidation },
          ] = await Promise.all([
            import("./components/Scaffolder/GitHubRepoUrlField/GitHubRepoUrlFieldExtension"),
            import("./components/Scaffolder/GitHubRepoUrlField/schema"),
            import("./components/Scaffolder/GitHubRepoUrlField/validation"),
          ]);
          return {
            name: "GitHubRepoUrlField",
            component: GitHubRepoUrlFieldExtension,
            schema: GitHubRepoUrlFieldFieldSchema,
            validation: githubRepoUrlValidation,
          };
        },
      }),
    }),
    apmeQualityTab,
    CatalogTableColumnBlueprint.make({
      name: "violations",
      params: {
        manifest: nfsManifest,
        capabilityId: capability.id,
        entryPointId: columnEntry.id,
        attachment: "catalog.table.columns",
        ...entityGates,
        tooltip:
          "Open quality violations and highest severity from APME scans.",
        loader: async ({ entity }) => {
          const { ApmeViolationsCell } =
            await import("./apis/apmeViolationsCell");
          return compatWrapper(<ApmeViolationsCell entity={entity} />);
        },
      },
    }),
    EntityActionBlueprint.make({
      name: "deregister",
      params: {
        ...removalPresentation,
        manifest: nfsManifest,
        capabilityId: lifecycleCapability.id,
        entryPointId: deregisterEntry.id,
        attachment: "entity.actions",
      },
    }),
    CatalogItemActionBlueprint.make({
      name: "deregister",
      params: {
        ...removalPresentation,
        manifest: nfsManifest,
        capabilityId: lifecycleCapability.id,
        entryPointId: catalogDeregisterEntry.id,
        attachment: "catalog.item.actions",
      },
    }),
    CatalogTabBlueprint.make({
      name: "fleet-quality",
      params: {
        manifest: nfsManifest,
        capabilityId: capability.id,
        entryPointId: fleetEntry.id,
        attachment: "catalog.tabs",
        ...portalGates,
        loader: withPortalGate(async ({ entityDetailPath }) => {
          const { FleetQualityTab } =
            await import("./components/FleetQualityTab");
          return compatWrapper(
            <FleetQualityTab
              repositoryDetailPath={(name, rule) =>
                entityDetailPath(
                  name,
                  qualityEntry.id,
                  rule ? { rule } : undefined,
                )
              }
            />,
          );
        }),
      },
    }),
    CatalogTabBlueprint.make({
      name: "quality-settings",
      params: {
        manifest: nfsManifest,
        capabilityId: settingsCapability.id,
        entryPointId: settingsEntry.id,
        attachment: "catalog.tabs",
        ...lifecycleGates,
        loader: withPortalGate(async () => {
          const [{ ApmeQualitySettingsTab }, { ApmeRulesTab }, { Box }] =
            await Promise.all([
              import("./components/ApmeQualitySettingsTab"),
              import("./components/ApmeRulesTab"),
              import("@material-ui/core"),
            ]);
          return compatWrapper(
            <>
              <ApmeQualitySettingsTab />
              <Box mt={4}>
                <ApmeRulesTab />
              </Box>
            </>,
          );
        }, []),
      },
    }),
    apmeScanAction,
    EntityDetailTabBlueprint.make({
      name: "quality-activity",
      params: {
        manifest: nfsManifest,
        capabilityId: capability.id,
        entryPointId: activityEntry.id,
        attachment: "entity.detail.tabs",
        ...entityGates,
        requiredEntitlements: ["apme.scanning"],
        loader: async ({ entity }) => {
          const { EntityQualityActivityTab } =
            await import("./components/EntityQualityActivityTab");
          return compatWrapper(<EntityQualityActivityTab entity={entity} />);
        },
      },
    }),
    EntityDetailTabBlueprint.make({
      name: "dependencies",
      params: {
        manifest: nfsManifest,
        capabilityId: dependenciesCapability.id,
        entryPointId: dependenciesEntry.id,
        attachment: "entity.detail.tabs",
        ...entityGates,
        loader: async ({ entity }) => {
          const { DependenciesTab } =
            await import("./components/DependenciesTab/DependenciesTab");
          return compatWrapper(
            <DependenciesTab
              context={{ entity, repoUrl: normalizeRepoUrlFromEntity(entity) }}
            />,
          );
        },
      },
    }),
    ScaffolderWorkflowBlueprint.make({
      name: "add-repository",
      params: {
        workflowId: "apme.add-repository",
        templateRef: "template:default/apme-register-git-repository",
      },
    }),
    CatalogHeaderActionBlueprint.make({
      name: "add-repository",
      params: {
        manifest: nfsManifest,
        capabilityId: lifecycleCapability.id,
        entryPointId: addRepositoryEntry.id,
        attachment: "catalog.header.actions",
        ...lifecycleGates,
      },
    }),
    CatalogItemActionBlueprint.make({
      name: "scan-catalog-item",
      params: {
        manifest: nfsManifest,
        capabilityId: capability.id,
        entryPointId: catalogScanEntry.id,
        attachment: "catalog.item.actions",
        ...entityGates,
        requiredEntitlements: ["apme.scanning"],
      },
    }),
    EntityOverviewCardBlueprint.make({
      name: "quality-summary",
      params: {
        manifest: nfsManifest,
        capabilityId: capability.id,
        entryPointId: overviewEntry.id,
        attachment: "entity.overview.secondary",
        detailsEntryPointId: qualityEntry.id,
        ...entityGates,
        requiredEntitlements: ["apme.scanning"],
        loader: async ({ entity, openDetails }) => {
          const { ApmeRepositoryOverviewCard } =
            await import("./components/ApmeRepositoryOverviewCard/ApmeRepositoryOverviewCard");
          return compatWrapper(
            <ApmeRepositoryOverviewCard
              context={{ entity }}
              onOpenQuality={openDetails}
              onOpenLatestScan={openDetails}
              navigationDisabled={!openDetails}
            />,
          );
        },
      },
    }),
  ],
});

// No import of plugin.ts or index.ts: NFS owns discovery and lifecycle.
export default apmePlugin;
