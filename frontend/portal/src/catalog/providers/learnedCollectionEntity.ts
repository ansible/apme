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

import { createHash } from "node:crypto";
import { Entity } from "@backstage/catalog-model";
import type { CollectionRef } from "../../common/index";

/** Annotation linking a learned (or usage) collection entity to a git-repo. */
export const CONSUMED_BY_REPOSITORY_ANNOTATION =
  "ansible.io/consumed-by-repository";

/** Optional ref to an existing catalog collection (PAH/SCM). */
export const CANONICAL_COLLECTION_ANNOTATION =
  "ansible.io/canonical-collection";

export const LEARNED_COLLECTION_SOURCE = "learned";

/** Ansible collection namespace: lowercase letter, then [a-z0-9_]*. */
const COLLECTION_NAMESPACE_RE = /^[a-z][a-z0-9_]*$/;

const MAX_ENTITY_NAME_LENGTH = 63;
const LEARNED_NAME_PREFIX = "apme-learned-";
/** Hex chars from sha256(repo|fqcn|version) — kept at the end so truncate cannot drop identity. */
const LEARNED_NAME_HASH_LENGTH = 8;

export function sanitizeLearnedEntityName(value: string): string {
  return value
    .toLowerCase()
    .replaceAll(/[^a-z0-9-]/g, "-")
    .replaceAll(/-+/g, "-")
    .replaceAll(/(^-)|(-$)/g, "")
    .substring(0, MAX_ENTITY_NAME_LENGTH);
}

/** Sanitize and truncate a name segment without chopping mid-hyphen trail. */
function truncateNameSegment(value: string, maxLength: number): string {
  if (maxLength <= 0) {
    return "";
  }
  return sanitizeLearnedEntityName(value)
    .substring(0, maxLength)
    .replace(/-$/, "");
}

export function parseCollectionFqcn(fqcn: string): {
  namespace: string;
  name: string;
} | null {
  const parts = fqcn.trim().split(".");
  if (parts.length < 2 || !parts[0] || !parts[1]) {
    return null;
  }
  return { namespace: parts[0], name: parts.slice(1).join(".") };
}

export function collectionFqcnKey(fullName: string, version?: string): string {
  const v = (version ?? "").trim();
  return v ? `${fullName}@${v}` : fullName;
}

/**
 * Stable Backstage entity name for a learned dep.
 * Format: `apme-learned-{short-repo}-{short-fqcn}-{8hex}` where the hash is
 * sha256(`repo|fqcn|version`) so the 63-char limit cannot collide versions/FQCNs.
 */
export function buildLearnedCollectionEntityName(
  repoEntityName: string,
  fqcn: string,
  version: string,
): string {
  const ver = version?.trim() || "unknown";
  const hash = createHash("sha256")
    .update(`${repoEntityName}|${fqcn}|${ver}`)
    .digest("hex")
    .slice(0, LEARNED_NAME_HASH_LENGTH);

  // Reserve prefix + two separators + trailing hash.
  const budget =
    MAX_ENTITY_NAME_LENGTH -
    LEARNED_NAME_PREFIX.length -
    LEARNED_NAME_HASH_LENGTH -
    2;
  const repoBudget = Math.max(1, Math.floor(budget * 0.6));
  const fqcnBudget = Math.max(1, budget - repoBudget);

  const parsed = parseCollectionFqcn(fqcn);
  const fqcnLabel = parsed ? `${parsed.namespace}.${parsed.name}` : fqcn;

  const shortRepo = truncateNameSegment(repoEntityName, repoBudget);
  const shortFqcn = truncateNameSegment(fqcnLabel, fqcnBudget);

  return `${LEARNED_NAME_PREFIX}${shortRepo}-${shortFqcn}-${hash}`;
}

export interface BuildLearnedCollectionEntityOptions {
  repoEntity: Entity;
  projectId: string;
  collection: CollectionRef;
  /** Existing catalog entity name for the same FQCN+version, if any. */
  canonicalEntityName?: string;
}

/** Builds a synthetic ansible-collection Component for an APME learned dep. */
export function buildLearnedCollectionEntity(
  options: BuildLearnedCollectionEntityOptions,
): Entity | null {
  const { repoEntity, projectId, collection, canonicalEntityName } = options;
  const repoName = repoEntity.metadata?.name;
  if (!repoName) {
    return null;
  }

  const parsed = parseCollectionFqcn(collection.fqcn);
  if (!parsed) {
    return null;
  }

  const version = collection.version?.trim() || "unknown";
  const entityName = buildLearnedCollectionEntityName(
    repoName,
    collection.fqcn,
    version,
  );
  const fullName = `${parsed.namespace}.${parsed.name}`;
  const title =
    version && version !== "unknown" ? `${fullName} v${version}` : fullName;

  const annotations: Record<string, string> = {
    [CONSUMED_BY_REPOSITORY_ANNOTATION]: repoName,
    "ansible.io/collection-source": LEARNED_COLLECTION_SOURCE,
    "ansible.io/apme-project-id": projectId,
    "ansible.io/apme-dependency-source": collection.source || "learned",
    "backstage.io/managed-by-location": `apme-learned-deps:${repoName}`,
    "backstage.io/managed-by-origin-location": `apme-learned-deps:${repoName}`,
  };

  if (canonicalEntityName) {
    annotations[CANONICAL_COLLECTION_ANNOTATION] =
      `component:default/${canonicalEntityName}`;
  }

  // Do not copy the consuming repo's scm-* annotations — that makes Collections
  // buildSourceString look like the collection lives in the playbook repo.
  // Linkage is via CONSUMED_BY_REPOSITORY_ANNOTATION only.

  const namespaceSafe = COLLECTION_NAMESPACE_RE.test(parsed.namespace);
  const owner = namespaceSafe ? parsed.namespace : "guest";
  const system = namespaceSafe
    ? `${parsed.namespace}-collections`
    : "learned-collections";

  return {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name: entityName,
      namespace: "default",
      title,
      description: `Learned dependency of ${repoName}: ${fullName}`,
      annotations,
      tags: ["ansible-collection", "learned-dependency"],
    },
    spec: {
      type: "ansible-collection",
      lifecycle: "production",
      owner,
      system,
      collection_namespace: parsed.namespace,
      collection_name: parsed.name,
      collection_version: version === "unknown" ? "" : version,
      collection_full_name: fullName,
    },
  };
}

/** Index catalog collections by FQCN[@version] for canonical matching. */
export function indexCatalogCollectionsByFqcn(
  collections: Entity[],
): Map<string, Entity> {
  const index = new Map<string, Entity>();
  for (const entity of collections) {
    const annotations = entity.metadata?.annotations ?? {};
    if (
      annotations["ansible.io/collection-source"] === LEARNED_COLLECTION_SOURCE
    ) {
      continue;
    }
    const spec = (entity.spec ?? {}) as {
      collection_full_name?: string;
      collection_version?: string;
    };
    const fullName = spec.collection_full_name;
    if (typeof fullName !== "string" || !fullName) {
      continue;
    }
    const version =
      typeof spec.collection_version === "string"
        ? spec.collection_version
        : "";
    index.set(collectionFqcnKey(fullName, version), entity);
    // Also index by FQCN alone for version-less lookups (first wins).
    if (!index.has(fullName)) {
      index.set(fullName, entity);
    }
  }
  return index;
}

export function findCanonicalCollectionEntity(
  index: Map<string, Entity>,
  fqcn: string,
  version: string,
): Entity | undefined {
  const withVersion = collectionFqcnKey(fqcn, version);
  return index.get(withVersion) ?? index.get(fqcn);
}
