/*
 * Copyright Red Hat
 *
 * Shared Git Repositories extension API for monolith (lazy) and OCI (direct) wiring.
 * eap-next thin host: Quality + Quality activity detail tabs, Fleet Quality +
 * settings page tabs, Overview quality card, Run quality scan, status chrome.
 */

import { Suspense, type ComponentType } from "react";
import { Box } from "@material-ui/core";
import {
  AnyApiFactory,
  configApiRef,
  createApiFactory,
} from "@backstage/core-plugin-api";
import { Entity } from "@backstage/catalog-model";
import { isApmeEnabled } from "../../common/config";
import {
  DefaultGitRepositoriesExtensionsApi,
  gitRepositoriesExtensionsApiRef,
  type GitRepositoriesExtensionsApi,
  type GitRepositoriesPageTabContext,
  type GitRepositoryDetailTabContext,
  type GitRepositoryDetailHeaderMenuContext,
  type GitRepositoryCatalogColumnDefinition,
  type GitRepositoryCatalogRowMenuContext,
} from "@ansible/backstage-rhaap-common/gitRepositoriesExtensions";
import { normalizeRepoUrlFromEntity } from "@ansible/backstage-rhaap-common/catalogEntity";
import { ansibleSettingsViewPermission } from "@ansible/backstage-rhaap-common/permissions";
import { ApmeAddRepositoryHeaderAction } from "../components/ApmeAddRepositoryHeaderAction/ApmeAddRepositoryHeaderAction";
import { ApmeQualitySettingsTab } from "../components/ApmeQualitySettingsTab";
import { ApmeRulesTab } from "../components/ApmeRulesTab";

export function withSuspense<P extends object>(
  Component: ComponentType<P>,
): ComponentType<P> {
  return function SuspenseWrapped(props: P) {
    return (
      <Suspense fallback={null}>
        <Component {...props} />
      </Suspense>
    );
  };
}

export type ApmeGitRepositoriesComponents = {
  FleetQualityTab: ComponentType<{
    repositoryDetailPath: GitRepositoriesPageTabContext["repositoryDetailPath"];
  }>;
  EntityQualityTab: ComponentType<{
    entity: GitRepositoryDetailTabContext["entity"];
    initialRuleFilter?: string;
    initialCategoryFilter?: string;
  }>;
  EntityQualityActivityTab: ComponentType<{
    entity: GitRepositoryDetailTabContext["entity"];
  }>;
  DependenciesTab: ComponentType<{ context: GitRepositoryDetailTabContext }>;
  ApmeRepositoryOverviewCard: ComponentType<{
    context: { entity: Entity };
  }>;
  ApmeRepositoryHeaderActions: ComponentType<{
    context: GitRepositoryDetailHeaderMenuContext;
    onCloseMenu: () => void;
    showDeregister?: boolean;
  }>;
  ApmeDeregisterRepositoryOverlay: ComponentType<{
    context?: GitRepositoryDetailTabContext;
  }>;
  ApmeViolationsCell: ComponentType<{ entity: Entity }>;
};

export function createApmeGitRepositoriesExtensionsApi(
  components: ApmeGitRepositoriesComponents,
): new () => GitRepositoriesExtensionsApi {
  const {
    FleetQualityTab,
    EntityQualityTab,
    EntityQualityActivityTab,
    DependenciesTab,
    ApmeRepositoryOverviewCard,
    ApmeRepositoryHeaderActions,
    ApmeDeregisterRepositoryOverlay,
    ApmeViolationsCell,
  } = components;

  return class ApmeGitRepositoriesExtensionsApi
    extends DefaultGitRepositoriesExtensionsApi
    implements GitRepositoriesExtensionsApi
  {
    getPageHeaderActions() {
      return [
        {
          id: "add-repository",
          order: 10,
          render: () => <ApmeAddRepositoryHeaderAction />,
        },
      ];
    }

    getPageTabs() {
      return [
        {
          id: "quality",
          label: "Quality",
          path: "quality",
          order: 10,
          render: ({ repositoryDetailPath }: GitRepositoriesPageTabContext) => (
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          ),
        },
        {
          id: "quality-settings",
          label: "Quality settings",
          path: "quality-settings",
          order: 15,
          render: () => (
            <>
              <ApmeQualitySettingsTab />
              <Box mt={4}>
                <ApmeRulesTab />
              </Box>
            </>
          ),
          // Hidden entirely (not just content-blocked) for users lacking
          // ansible.settings.view for apme. Nesting Rules here is intentional
          // (AAP-88784): the old standalone rules tab was only gated by
          // gitRepositoriesViewPermission; the consolidated tab uses the
          // Quality settings permission.
          permission: ansibleSettingsViewPermission,
          resourceRef: "apme",
        },
      ];
    }

    getDetailTabs() {
      return [
        {
          id: "quality",
          label: "Quality",
          order: 10,
          render: ({
            entity,
            initialRuleFilter,
            initialCategoryFilter,
          }: GitRepositoryDetailTabContext) => (
            <EntityQualityTab
              entity={entity}
              initialRuleFilter={initialRuleFilter}
              initialCategoryFilter={initialCategoryFilter}
            />
          ),
        },
        {
          id: "quality-activity",
          label: "Quality activity",
          order: 15,
          render: ({ entity }: GitRepositoryDetailTabContext) => (
            <EntityQualityActivityTab entity={entity} />
          ),
        },
        {
          id: "dependencies",
          label: "Dependencies",
          order: 16,
          render: (ctx: GitRepositoryDetailTabContext) => (
            <DependenciesTab context={ctx} />
          ),
        },
      ];
    }

    getDetailOverviewSlots() {
      return [
        {
          id: "apme-quality-overview",
          order: 10,
          render: (ctx: GitRepositoryDetailTabContext) => (
            <ApmeRepositoryOverviewCard context={ctx} />
          ),
        },
      ];
    }

    getDetailHeaderMenuItems() {
      return [
        {
          id: "apme-header-actions",
          order: 10,
          render: (ctx: GitRepositoryDetailHeaderMenuContext) => (
            <ApmeRepositoryHeaderActions
              context={ctx}
              onCloseMenu={ctx.onCloseMenu}
            />
          ),
        },
      ];
    }

    getDetailOverlays() {
      return [
        {
          id: "apme-deregister-repository",
          order: 10,
          render: (ctx: GitRepositoryDetailTabContext) => (
            <ApmeDeregisterRepositoryOverlay context={ctx} />
          ),
        },
      ];
    }

    /**
     * Catalog row kebab reuses ApmeRepositoryHeaderActions for "Run quality scan"
     * and Deregister (manual repos with delete permission).
     */
    getCatalogRowMenuItems() {
      return [
        {
          id: "apme-run-quality-scan",
          order: 10,
          render: (ctx: GitRepositoryCatalogRowMenuContext) => (
            <ApmeRepositoryHeaderActions
              context={{
                entity: ctx.entity,
                repoUrl: normalizeRepoUrlFromEntity(ctx.entity),
                onCloseMenu: ctx.onCloseMenu,
                repositoriesCatalogPath: ctx.repositoriesCatalogPath,
              }}
              onCloseMenu={ctx.onCloseMenu}
            />
          ),
        },
      ];
    }

    getCatalogOverlays() {
      return [
        {
          id: "apme-deregister-repository",
          order: 10,
          render: () => <ApmeDeregisterRepositoryOverlay />,
        },
      ];
    }

    /** Parity with prototype: no status chips on the catalog list (noisy). */
    getCatalogRowSlots() {
      return [];
    }

    getCatalogColumns(): GitRepositoryCatalogColumnDefinition[] {
      return [
        {
          id: "violations",
          title: "Violations",
          tooltip:
            "Content quality violations detected by APME scanning. Shows open violation count and highest severity.",
          order: 10,
          render: (entity: Entity) => <ApmeViolationsCell entity={entity} />,
        },
      ];
    }
  };
}

export function createGitRepositoriesExtensionsApiFactory(
  ApiClass: new () => GitRepositoriesExtensionsApi,
): AnyApiFactory {
  return createApiFactory({
    api: gitRepositoriesExtensionsApiRef,
    deps: { configApi: configApiRef },
    factory: ({ configApi }) =>
      isApmeEnabled(configApi)
        ? new ApiClass()
        : new DefaultGitRepositoriesExtensionsApi(),
  });
}
