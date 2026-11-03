import type { PluginManifest } from "@ansible/portal-extension-api";
import {
  apmeQualityScanOperation,
  apmeRepositoryDeregisterOperation,
  apmeRepositoryRegisterOperation,
  apmeRepositoryRegisterPermission,
  apmeScanningEntitlement,
  apmeSettingsGetOperation,
  apmeSettingsSaveOperation,
} from "../common/operations";
import { EXPERIENCE_IDS, CONTENT_TYPES } from "@ansible/portal-extension-api";
import { ansibleSettingsViewPermission } from "@ansible/backstage-rhaap-common/permissions";

/**
 * The host contract version APME was built against.
 * Must match the host's HOST_API_VERSION in validateManifest.ts.
 */
export const APME_API_VERSION = "0.1.0";

/**
 * APME plugin manifest — declares every capability this plugin contributes
 * to the Automation Portal.
 *
 * APME (Ansible Policy and Modernization Engine) adds quality assessment,
 * lifecycle management, and dependency analysis to playbook repositories.
 *
 * ### Migration note (ANSTRAT-2497)
 * This manifest replaces the ADR-010 `gitRepositoriesExtensionsApiRef` contract.
 * Previously APME registered its tabs by implementing `GitRepositoriesExtensionsApi`
 * and registering via Backstage's DI factory system. The portal SDK replaces that
 * with a declarative manifest + `registerManifest()` call at plugin load time.
 *
 * ### Slot targets
 * `targetSlot` values mirror the named slots declared in `portal-extension-host`'s
 * `ExperienceSlot` for the playbook-repository entity page and list page.
 * Phase 6 (ANSTRAT-2497): slot-based activation replaces the current direct-render
 * wiring once NFS mount point design with Ganesh is complete.
 */
export const apmeManifest: PluginManifest = {
  id: "apme",
  version: "1.0.0",
  apiVersion: APME_API_VERSION,

  capabilities: [
    // ── Quality assessment ─────────────────────────────────────────────────
    // All quality scanning, violation review, and activity history surfaces.
    {
      id: "apme.quality-assessment",
      ownerPlugin: "apme",
      experienceId: EXPERIENCE_IDS.CONTENT_QUALITY,
      displayName: "Repository Quality Assessment",
      description:
        "Content quality scanning, violation review, and activity history for playbook repositories. " +
        "Adds fleet-level and per-entity quality tabs, an overview quality card, " +
        "and a violations column to the repository catalog.",
      appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
      entryPoints: [
        {
          id: "apme.quality-assessment.scan-action",
          kind: "entity-action",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Scan for quality issues",
          order: 10,
          requiredPermission: apmeQualityScanOperation.permission,
          launches: {
            type: "operation",
            operationId: apmeQualityScanOperation.id,
            followOn: { type: "slot", targetSlot: "entity.detail.tabs" },
          },
        },
        // List-level: Fleet Quality tab on the Git Repositories page
        {
          id: "apme.quality-assessment.fleet-quality-tab",
          kind: "page-tab",
          surface: "experience-slot",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality",
          order: 10,
          launches: {
            type: "slot",
            targetSlot: "repository-list.fleet-quality-tab",
          },
        },
        // Entity detail: Quality tab (violations breakdown)
        {
          id: "apme.quality-assessment.quality-tab",
          kind: "entity-tab",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality",
          order: 10,
          launches: {
            type: "slot",
            targetSlot: "repository-detail.quality-tab",
          },
        },
        // Entity detail: Quality Activity tab (scan history)
        {
          id: "apme.quality-assessment.quality-activity-tab",
          kind: "entity-tab",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality activity",
          order: 15,
          launches: {
            type: "slot",
            targetSlot: "repository-detail.quality-activity-tab",
          },
        },
        // Entity detail: Overview sidebar quality summary card
        {
          id: "apme.quality-assessment.overview-card",
          kind: "overview-slot",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality summary",
          order: 10,
          launches: {
            type: "slot",
            targetSlot: "repository-detail.overview-quality-card",
          },
        },
        // Catalog list: Violations count column
        {
          id: "apme.quality-assessment.violations-column",
          kind: "table-column",
          surface: "catalog-item",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Violations",
          order: 10,
          launches: {
            type: "slot",
            targetSlot: "repository-catalog.violations-column",
          },
        },
        // Catalog list: Run quality scan kebab item + deregister (row-level)
        {
          id: "apme.quality-assessment.catalog-row-actions",
          kind: "catalog-item-action",
          surface: "catalog-item",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality actions",
          order: 10,
          launches: {
            type: "slot",
            targetSlot: "repository-catalog.row-actions",
          },
        },
      ],
      minimumHostApiVersion: "0.1.0",
    },

    // ── Quality settings ───────────────────────────────────────────────────
    // Quality configuration, rules management. Requires settings permissions.
    {
      id: "apme.quality-settings",
      ownerPlugin: "apme",
      experienceId: EXPERIENCE_IDS.CONTENT_QUALITY,
      displayName: "Repository Quality Settings",
      description:
        "Quality rule configuration and AI provider settings for APME scanning. " +
        "Visible only to users with ansible.settings.view on the apme capability.",
      appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
      entryPoints: [
        {
          id: "apme.quality-settings.settings-tab",
          kind: "page-tab",
          surface: "experience-slot",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Quality settings",
          order: 15,
          launches: {
            type: "slot",
            targetSlot: "repository-list.quality-settings-tab",
          },
          requiredPermission: {
            permission: ansibleSettingsViewPermission,
            resourceRef: "apme",
          },
        },
      ],
      minimumHostApiVersion: "0.1.0",
    },

    // ── Repository lifecycle ───────────────────────────────────────────────
    // Register and deregister playbook repositories for APME scanning.
    {
      id: "apme.repository-lifecycle",
      ownerPlugin: "apme",
      experienceId: EXPERIENCE_IDS.CONTENT_AUTHORING,
      displayName: "Repository Lifecycle",
      description:
        "Register new repositories with the Portal for APME quality scanning, " +
        "and deregister manually-added repositories. " +
        "Registration and deregistration require their respective repository mutation permissions.",
      appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
      entryPoints: [
        // List-level header: "Add repository" scaffolder launcher
        {
          id: "apme.github-repo-url-field",
          kind: "scaffolder-field",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          surface: "experience-slot",
          label: "GitHub repository URL",
          launches: { type: "slot", targetSlot: "scaffolder.fields" },
        },
        {
          id: "apme.repository-lifecycle.add-repository",
          kind: "entity-action",
          surface: "experience-slot",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Add repository",
          order: 10,
          launches: {
            // Launches the add-repository scaffolder template.
            // Phase 6: workflowId maps to a registered scaffolder workflow.
            type: "workflow",
            workflowId: "apme.add-repository",
          },
          requiredPermission: apmeRepositoryRegisterPermission,
        },
        // Entity detail: Deregister dialog (survives menu close)
        {
          id: "apme.repository-lifecycle.deregister-overlay",
          kind: "overlay",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Deregister repository",
          order: 10,
          launches: {
            type: "operation",
            operationId: "apme.repository.deregister",
          },
        },
        // Catalog list: Deregister dialog overlay (outside kebab)
        {
          id: "apme.repository-lifecycle.catalog-deregister-overlay",
          kind: "overlay",
          surface: "catalog-item",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Deregister repository",
          order: 10,
          launches: {
            type: "operation",
            operationId: "apme.repository.deregister",
          },
        },
      ],
      minimumHostApiVersion: "0.1.0",
    },

    // ── Dependency analysis ────────────────────────────────────────────────
    // Dependency graph and resolution for playbook repositories.
    {
      id: "apme.dependencies",
      ownerPlugin: "apme",
      experienceId: EXPERIENCE_IDS.CONTENT_AUTHORING,
      displayName: "Repository Dependencies",
      description:
        "Dependency analysis for playbook repositories — shows resolved " +
        "collections, roles, and Python requirements detected by APME.",
      appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
      entryPoints: [
        {
          id: "apme.dependencies.dependencies-tab",
          kind: "entity-tab",
          surface: "entity-page",
          appliesToContentTypes: [CONTENT_TYPES.PLAYBOOK_REPOSITORY],
          label: "Dependencies",
          order: 16,
          launches: {
            type: "slot",
            targetSlot: "repository-detail.dependencies-tab",
          },
        },
      ],
      minimumHostApiVersion: "0.1.0",
    },
  ],

  entitlements: [apmeScanningEntitlement],

  operations: [
    apmeQualityScanOperation,
    apmeRepositoryRegisterOperation,
    apmeRepositoryDeregisterOperation,
    apmeSettingsGetOperation,
    apmeSettingsSaveOperation,
  ],
};
