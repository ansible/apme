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

import { useMemo } from "react";
import type { Entity } from "@backstage/catalog-model";
import { EntityProvider } from "@backstage/plugin-catalog-react";
import { ApmeEntityTab } from "../ApmeEntityTab";

export interface QualityTabProps {
  repoUrl?: string | null;
  branch?: string;
  projectId?: string;
  /** Pre-filter violations by rule (Inc 10 fleet drill-down). */
  initialRuleFilter?: string;
}

function buildQualityTabEntity(repoUrl: string, branch: string): Entity {
  const name =
    repoUrl
      .replace(/\/$/, "")
      .split("/")
      .pop()
      ?.replace(/\.git$/, "") ?? "repository";

  return {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name,
      title: name,
      annotations: {
        "backstage.io/source-location": `url:${repoUrl}`,
      },
    },
    spec: {
      type: "git-repository",
      repository_default_branch: branch,
    },
  };
}

export const QualityTab = ({
  repoUrl,
  branch = "main",
  projectId,
  initialRuleFilter,
}: QualityTabProps) => {
  const entity = useMemo(() => {
    if (repoUrl) {
      return buildQualityTabEntity(repoUrl, branch);
    }
    if (projectId) {
      return {
        apiVersion: "backstage.io/v1alpha1",
        kind: "Component",
        metadata: {
          name: `apme-project-${projectId}`,
          title: `apme-project-${projectId}`,
        },
        spec: {
          type: "git-repository",
          repository_default_branch: branch,
        },
      } satisfies Entity;
    }
    return null;
  }, [repoUrl, branch, projectId]);

  if (!entity) {
    return null;
  }

  return (
    <EntityProvider entity={entity}>
      <ApmeEntityTab initialRuleFilter={initialRuleFilter} />
    </EntityProvider>
  );
};
