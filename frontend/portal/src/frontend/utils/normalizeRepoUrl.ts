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

import { Entity } from "@backstage/catalog-model";

/** Normalizes catalog annotations to a clone URL APME can match. */
export function normalizeRepoUrlFromEntity(entity: Entity): string | null {
  const annotations = entity.metadata.annotations ?? {};
  const sourceLocation = annotations["backstage.io/source-location"];
  if (sourceLocation) {
    return normalizeSourceLocation(sourceLocation);
  }

  const projectSlug = annotations["github.com/project-slug"];
  if (projectSlug) {
    return `https://github.com/${projectSlug}`;
  }

  const scmHost = annotations["ansible.io/scm-host"];
  const scmOrg = annotations["ansible.io/scm-organization"];
  const scmRepo = annotations["ansible.io/scm-repository"];
  if (scmHost && scmOrg && scmRepo) {
    const host = scmHost.replace(/^https?:\/\//, "").replace(/\/$/, "");
    return `https://${host}/${scmOrg}/${scmRepo}`;
  }

  return null;
}

/** Normalizes a raw annotation value to a repository root URL. */
export function normalizeSourceLocation(sourceLocation: string): string | null {
  let value = sourceLocation.trim();
  if (value.startsWith("url:")) {
    value = value.slice(4).trim();
  }

  try {
    const url = new URL(value);
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments.length >= 2) {
      return `${url.protocol}//${url.host}/${segments[0]}/${segments[1]}`;
    }
    return `${url.protocol}//${url.host}${url.pathname}`.replace(/\/$/, "");
  } catch {
    if (value.includes("/")) {
      const [org, repo] = value.split("/");
      if (org && repo) {
        return `https://github.com/${org}/${repo.split("/")[0]}`;
      }
    }
    return null;
  }
}
