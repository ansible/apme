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

import { Button } from "@patternfly/react-core";
import { CodeIcon } from "@patternfly/react-icons";

export interface EditInDevSpacesButtonProps {
  url: string | null | undefined;
  label?: string;
  variant?: "primary" | "secondary" | "tertiary" | "link";
}

/** Opens the repo (optional branch) in OpenShift Dev Spaces when configured. */
export const EditInDevSpacesButton = ({
  url,
  label = "Open in Dev Spaces",
  variant = "secondary",
}: EditInDevSpacesButtonProps) => {
  if (!url) {
    return null;
  }
  return (
    <Button
      variant={variant}
      component="a"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      icon={<CodeIcon />}
    >
      {label}
    </Button>
  );
};
