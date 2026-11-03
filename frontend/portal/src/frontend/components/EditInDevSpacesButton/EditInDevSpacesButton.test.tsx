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

import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { EditInDevSpacesButton, PostPushDevSpacesBanner } from "./index";

describe("EditInDevSpacesButton", () => {
  it("renders nothing without a URL", () => {
    const { container } = render(<EditInDevSpacesButton url={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("links to the Dev Spaces factory URL", () => {
    render(
      <EditInDevSpacesButton url="https://devspaces.example.com#https://github.com/acme/repo/tree/fix" />,
    );
    const link = screen.getByRole("link", { name: /open in dev spaces/i });
    expect(link).toHaveAttribute(
      "href",
      "https://devspaces.example.com#https://github.com/acme/repo/tree/fix",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });
});

describe("PostPushDevSpacesBanner", () => {
  it("renders nothing without a URL", () => {
    const { container } = render(<PostPushDevSpacesBanner url={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows branch copy and Dev Spaces link after push", () => {
    render(
      <PostPushDevSpacesBanner
        url="https://devspaces.example.com#https://github.com/acme/repo/tree/apme/remediate-abc"
        branchName="apme/remediate-abc"
      />,
    );
    expect(screen.getByText(/apme\/remediate-abc/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open in dev spaces/i }),
    ).toHaveAttribute(
      "href",
      "https://devspaces.example.com#https://github.com/acme/repo/tree/apme/remediate-abc",
    );
  });
});
