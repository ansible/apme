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
import {
  createProjectRequestFromEntity,
  entityMatchesApmeOrgScope,
  selectEntitiesForApmeSync,
  sliceApmeSyncBatch,
} from "./apmeCatalogSync";
import { ApmeGitContentsSyncConfig } from "./apmeSyncConfig";

const baseEntity = (
  org: string,
  name: string,
  tags: string[] = [],
  host = "github.com",
  provider = "github",
): Entity =>
  ({
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name,
      uid: name,
      tags,
      annotations: {
        "backstage.io/source-location": `url:https://${host}/${org}/${name}`,
        "ansible.io/scm-organization": org,
        "ansible.io/scm-provider": provider,
      },
    },
    spec: {
      type: "git-repository",
      repository_default_branch: "main",
    },
  }) as Entity;

describe("apmeCatalogSync helpers", () => {
  const syncConfig: ApmeGitContentsSyncConfig = {
    env: "development",
    enabled: true,
    scanOnRegister: false,
    maxPerRun: 2,
    orgs: [
      {
        env: "development",
        provider: "github",
        host: "github.com",
        organization: "acme-scm",
        scanOnRegister: true,
      },
      {
        env: "development",
        provider: "github",
        host: "github.com",
        organization: "ansible-demo",
        scanOnRegister: false,
        labels: ["github"],
      },
    ],
  };

  it("entityMatchesApmeOrgScope respects label filters", () => {
    const scope = syncConfig.orgs[1];
    expect(
      entityMatchesApmeOrgScope(
        baseEntity("ansible-demo", "repo-a", ["github"]),
        scope,
      ),
    ).toBe(true);
    expect(
      entityMatchesApmeOrgScope(
        baseEntity("ansible-demo", "repo-b", []),
        scope,
      ),
    ).toBe(false);
  });

  it("selectEntitiesForApmeSync filters by opted-in orgs", () => {
    const entities = [
      baseEntity("acme-scm", "amazon-aws"),
      baseEntity("other-org", "ignored"),
      baseEntity("ansible-demo", "network-firewall", ["github"]),
    ];

    expect(selectEntitiesForApmeSync(entities, syncConfig)).toHaveLength(2);
  });

  it("matches organization names only within the configured provider and host", () => {
    const scopedConfig: ApmeGitContentsSyncConfig = {
      ...syncConfig,
      orgs: [
        {
          env: "development",
          provider: "github",
          host: "github.com",
          organization: "same-org",
          scanOnRegister: false,
        },
      ],
    };
    const selected = selectEntitiesForApmeSync(
      [
        baseEntity("same-org", "github-repo"),
        baseEntity("same-org", "gitlab-repo", [], "gitlab.com", "gitlab"),
        baseEntity(
          "same-org",
          "enterprise-repo",
          [],
          "git.example.com",
          "gitlab",
        ),
      ],
      scopedConfig,
    );
    expect(selected.map((entity) => entity.metadata.name)).toEqual([
      "github-repo",
    ]);
  });

  it("sliceApmeSyncBatch rotates cursor across runs", () => {
    const entities = [
      baseEntity("acme-scm", "c"),
      baseEntity("acme-scm", "a"),
      baseEntity("acme-scm", "b"),
    ];
    const eligible = selectEntitiesForApmeSync(entities, syncConfig);

    const first = sliceApmeSyncBatch(eligible, 0, 2);
    expect(first.batch.map((e) => e.metadata.name)).toEqual(["a", "b"]);
    expect(first.nextOffset).toBe(2);
    expect(first.remaining).toBe(1);

    const second = sliceApmeSyncBatch(eligible, first.nextOffset, 2);
    expect(second.batch.map((e) => e.metadata.name)).toEqual(["c"]);
    expect(second.nextOffset).toBe(0);
    expect(second.remaining).toBe(0);
  });

  it("createProjectRequestFromEntity builds gateway payload", () => {
    const entity = baseEntity("acme-scm", "amazon-aws");
    expect(createProjectRequestFromEntity(entity)).toEqual({
      name: "amazon-aws",
      repo_url: "https://github.com/acme-scm/amazon-aws",
      branch: "main",
    });
  });
});
