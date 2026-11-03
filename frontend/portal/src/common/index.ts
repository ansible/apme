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

// This package is consumed by BOTH frontend and backend.
// @backstage/backend-plugin-api works in both (tree-shaken in frontend).
// @backstage/core-plugin-api is frontend-only and CRASHES the backend — DO NOT export here.
export * from "./types";
export * from "./severity";
export * from "./enrichProjectsWithSeverity";
export * from "./proposalTier";
export * from "./normalizeRepoUrl";
export * from "./catalogEntity";
export {
  apmeQualityScanPermission,
  apmeQualityScanOperation,
} from "./operations";
export * from "./ApmeService";
export * from "./config";
export * from "./apmeSyncConfig";
export * from "./apmeCatalogSync";
export * from "./operationStatus";
export {
  ansibleCoreVersionOptions,
  isAllowedAnsibleCoreVersion,
  normalizeAnsibleCoreVersion,
  ANSIBLE_CORE_VERSION_OPTIONS,
} from "./ansibleCoreVersionOptions";
export {
  resolveScanTarget,
  resolveScanTargetVersion,
} from "./resolveScanTarget";
export {
  InvalidGatewayBaseUrlError,
  normalizeGatewayBaseUrl,
  resolveGatewayBaseUrl,
} from "./resolveGatewayBaseUrl";
export type {
  GatewayBaseUrlResolution,
  GatewayBaseUrlSource,
  ResolveGatewayBaseUrlInput,
} from "./resolveGatewayBaseUrl";
export {
  formatApmeAbbenayChatModelId,
  pickApmeChatModelId,
  resolveApmeChatModelIdFromProviders,
} from "./apmeChatModel";
export type {
  ActivityPortalOutcome,
  ApmePortalSettingsData,
  ProjectScanTargetResolution,
  ScanTargetSource,
} from "./resolveScanTarget";
export { mergeActivityPortalOutcomes } from "./mergeActivityPortalOutcomes";
export {
  isApmeProjectConflictError,
  resolveApmeProject,
  registerOrResolveApmeProject,
} from "./registerOrResolveApmeProject";
export type { ApmeProjectResolver } from "./registerOrResolveApmeProject";
export {
  PORTAL_HUB_GALAXY_SERVER_PREFIX,
  buildPortalPahGalaxyServers,
  isPortalManagedGalaxyServerName,
  normalizePahRepoIdentifier,
  syncPortalGalaxyServers,
} from "./portalGalaxyServers";
export type {
  PortalPahGalaxyServerSpec,
  SyncPortalGalaxyServersResult,
} from "./portalGalaxyServers";
// FRONTEND-ONLY — import by path, NOT from this index:
//   import { apmeApiRef } from './api'
//   import { useApmeEnabled } from './useApmeEnabled'
