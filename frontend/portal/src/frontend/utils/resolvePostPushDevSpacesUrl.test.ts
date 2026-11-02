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

import { resolvePostPushDevSpacesUrl } from "./resolvePostPushDevSpacesUrl";

describe("resolvePostPushDevSpacesUrl", () => {
  const base = {
    sessionVisible: true,
    devSpacesBaseUrl: "https://devspaces.example.com",
    repoUrl: "https://github.com/acme/ansible-apme",
  };

  it("returns null when session is not visible", () => {
    expect(
      resolvePostPushDevSpacesUrl({
        ...base,
        sessionVisible: false,
        pushedBranchName: "apme/remediate-abc",
      }),
    ).toBeNull();
  });

  it("returns null before push/PR", () => {
    expect(resolvePostPushDevSpacesUrl(base)).toBeNull();
  });

  it("builds URL with remediation branch after push", () => {
    expect(
      resolvePostPushDevSpacesUrl({
        ...base,
        pushedBranchName: "apme/remediate-abc",
      }),
    ).toBe(
      "https://devspaces.example.com/#https://github.com/acme/ansible-apme/tree/apme/remediate-abc",
    );
  });

  it("uses the pull request URL when only pr_url is known", () => {
    expect(
      resolvePostPushDevSpacesUrl({
        ...base,
        prUrl: "https://github.com/acme/ansible-apme/pull/12",
      }),
    ).toBe(
      "https://devspaces.example.com/#https://github.com/acme/ansible-apme/pull/12",
    );
  });

  it("prefers pushed branch over pull request URL", () => {
    expect(
      resolvePostPushDevSpacesUrl({
        ...base,
        pushedBranchName: "apme/remediate-xyz",
        prUrl: "https://github.com/acme/ansible-apme/pull/12",
      }),
    ).toBe(
      "https://devspaces.example.com/#https://github.com/acme/ansible-apme/tree/apme/remediate-xyz",
    );
  });
});
