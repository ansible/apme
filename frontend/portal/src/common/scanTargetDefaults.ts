/*
 * Copyright Red Hat
 *
 * Frontend-safe default shared by the APME-owned Portal adapters.
 * (not the package root index, which re-exports backend code).
 */

/** Default ansible-core scan target when app-config omits `targetAnsibleCoreVersion`. */
export const DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION = "2.16";
