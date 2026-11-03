/*
 * Copyright Red Hat
 *
 * ADR-010: APME guest registers Git Repositories extension API for OCI/dynamic plugin mode.
 * Monolith uses packages/app/src/apis/gitRepositoriesExtensions.tsx — keep in sync.
 */

import { EntityQualityTab } from "../components/EntityQualityTab";
import { EntityQualityActivityTab } from "../components/EntityQualityActivityTab";
import { DependenciesTab } from "../components/DependenciesTab/DependenciesTab";
import { FleetQualityTab } from "../components/FleetQualityTab";
import { ApmeRepositoryOverviewCard } from "../components/ApmeRepositoryOverviewCard/ApmeRepositoryOverviewCard";
import { ApmeRepositoryHeaderActions } from "../components/ApmeRepositoryHeaderActions/ApmeRepositoryHeaderActions";
import { ApmeDeregisterRepositoryOverlay } from "../components/ApmeDeregisterRepositoryOverlay";
import { ApmeViolationsCell } from "./apmeViolationsCell";
import {
  createApmeGitRepositoriesExtensionsApi,
  createGitRepositoriesExtensionsApiFactory,
  withSuspense,
} from "./apmeGitRepositoriesExtensionsCore";

const ApmeGitRepositoriesExtensionsApi = createApmeGitRepositoriesExtensionsApi(
  {
    FleetQualityTab: withSuspense(FleetQualityTab),
    EntityQualityTab: withSuspense(EntityQualityTab),
    EntityQualityActivityTab: withSuspense(EntityQualityActivityTab),
    DependenciesTab: withSuspense(DependenciesTab),
    ApmeRepositoryOverviewCard: withSuspense(ApmeRepositoryOverviewCard),
    ApmeRepositoryHeaderActions: withSuspense(ApmeRepositoryHeaderActions),
    ApmeDeregisterRepositoryOverlay: withSuspense(
      ApmeDeregisterRepositoryOverlay,
    ),
    ApmeViolationsCell,
  },
);

export const gitRepositoriesExtensionsApiFactory =
  createGitRepositoriesExtensionsApiFactory(ApmeGitRepositoriesExtensionsApi);
