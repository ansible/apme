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

import { Flex, FlexItem } from "@patternfly/react-core";
import { EditInDevSpacesButton } from "./EditInDevSpacesButton";

export interface PostPushDevSpacesBannerProps {
  /** Dev Spaces factory URL; banner is hidden when null/undefined. */
  url: string | null | undefined;
  /** Remediation branch name shown in the banner copy. */
  branchName?: string;
}

/**
 * Thin post-push CTA shown above the workflow panel after commit/push.
 * Open/View PR remains inside `@apme/ui-workflow`.
 */
export const PostPushDevSpacesBanner = ({
  url,
  branchName,
}: PostPushDevSpacesBannerProps) => {
  if (!url) {
    return null;
  }
  return (
    <div
      style={{
        marginBottom: 16,
        padding: "12px 16px",
        border: "1px solid var(--pf-t--global--border--color--default)",
        borderRadius: 6,
        background:
          "var(--pf-t--global--background--color--secondary--default)",
      }}
    >
      <Flex
        alignItems={{ default: "alignItemsCenter" }}
        justifyContent={{ default: "justifyContentSpaceBetween" }}
        gap={{ default: "gapMd" }}
        flexWrap={{ default: "wrap" }}
      >
        <FlexItem>
          <span style={{ fontSize: 14 }}>
            {branchName ? (
              <>
                Remediation branch <strong>{branchName}</strong> is available.
                Open it in Dev Spaces to review or hand-edit before merging.
              </>
            ) : (
              <>
                Remediation changes were pushed. Open the codebase in Dev Spaces
                to review or hand-edit before merging.
              </>
            )}
          </span>
        </FlexItem>
        <FlexItem>
          <EditInDevSpacesButton url={url} />
        </FlexItem>
      </Flex>
    </div>
  );
};
