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

export {
  apmePlugin,
  ApmeEntityTab,
  ApmeEnabledEntityLayoutRoute,
  QualityTabExtension,
  rootRouteRef as apmeRouteRef,
} from "./plugin";
export { QualityTab } from "./components/QualityTab";
export { EntityQualityTab as ApmeEntityQualityTabComponent } from "./components/EntityQualityTab";
export { EntityQualityActivityTab as ApmeEntityQualityActivityTabComponent } from "./components/EntityQualityActivityTab";
export { DependenciesTab as ApmeDependenciesTabComponent } from "./components/DependenciesTab/DependenciesTab";
export { FleetQualityTab as ApmeFleetQualityTabComponent } from "./components/FleetQualityTab";
export type { FleetQualityTabProps as ApmeFleetQualityTabProps } from "./components/FleetQualityTab";
export { ApmeQualityActivityTab } from "./components/ApmeQualityActivityTab";
export { apmeApiRef, ApmeApiClient } from "./api";
export { apmeApiFactory } from "./api/apmeApiFactory";
export type { ApmeApi } from "./api";
export { createApmeUiWorkflowAdapter } from "./api/createApmeUiWorkflowAdapter";
export {
  normalizeRepoUrlFromEntity,
  defaultBranchFromEntity,
  scmOrganizationFromEntity,
} from "../common/catalogEntity";
export {
  normalizeSourceLocation,
  normalizeRepoUrl,
} from "../common/normalizeRepoUrl";
export { useApmeEnabled } from "./hooks/useApmeEnabled";
export { isGitRepositoryEntity } from "./utils/isGitRepositoryEntity";
export { gitRepositoriesExtensionsApiFactory } from "./apis/gitRepositoriesExtensions";
export {
  createApmeGitRepositoriesExtensionsApi,
  createGitRepositoriesExtensionsApiFactory,
  withSuspense,
} from "./apis/apmeGitRepositoriesExtensionsCore";
export { ApmeViolationsCell } from "./apis/apmeViolationsCell";
export { ApmeRepoStatusChip } from "./components/ApmeRepoStatusChip";
export { ApmeRepositoryHeaderActions } from "./components/ApmeRepositoryHeaderActions/ApmeRepositoryHeaderActions";
export { ApmeDeregisterRepositoryOverlay } from "./components/ApmeDeregisterRepositoryOverlay";
export { ApmeRepositoryOverviewCard } from "./components/ApmeRepositoryOverviewCard/ApmeRepositoryOverviewCard";
export { ApmeAddRepositoryHeaderAction } from "./components/ApmeAddRepositoryHeaderAction/ApmeAddRepositoryHeaderAction";
export { ApmeQualitySettingsTab } from "./components/ApmeQualitySettingsTab";
export { ApmeRulesTab } from "./components/ApmeRulesTab";
export { ApmeAbbenaySettingsTabRoute } from "./components/ApmeAbbenaySettingsTab";
export { APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH } from "./components/ApmeAddRepositoryHeaderAction/ApmeAddRepositoryHeaderAction";
export {
  PreviewChip,
  PreviewLabelRow,
  PreviewNotice,
  PreviewFeedbackLink,
} from "./components/PreviewChip";
