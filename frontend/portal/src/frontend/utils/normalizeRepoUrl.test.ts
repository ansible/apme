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
  normalizeRepoUrlFromEntity,
  normalizeSourceLocation,
} from "./normalizeRepoUrl";

describe("normalizeSourceLocation", () => {
  it("strips url: prefix and path segments", () => {
    expect(
      normalizeSourceLocation(
        "url:https://github.com/ansible/ansible-examples/tree/main",
      ),
    ).toBe("https://github.com/ansible/ansible-examples");
  });

  it("returns host path for shallow URLs", () => {
    expect(normalizeSourceLocation("https://gitlab.com/org/repo")).toBe(
      "https://gitlab.com/org/repo",
    );
  });
});

describe("normalizeRepoUrlFromEntity", () => {
  it("uses github project slug annotation", () => {
    const entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "demo",
        annotations: {
          "github.com/project-slug": "ansible/ansible-examples",
        },
      },
    } as Entity;

    expect(normalizeRepoUrlFromEntity(entity)).toBe(
      "https://github.com/ansible/ansible-examples",
    );
  });

  it("uses ansible scm annotations", () => {
    const entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "demo",
        annotations: {
          "ansible.io/scm-host": "gitlab.example.com",
          "ansible.io/scm-organization": "team",
          "ansible.io/scm-repository": "playbooks",
        },
      },
    } as Entity;

    expect(normalizeRepoUrlFromEntity(entity)).toBe(
      "https://gitlab.example.com/team/playbooks",
    );
  });
});
