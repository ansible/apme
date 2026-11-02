/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { ActivityPublishedCell } from "./ActivityPublishedCell";

jest.mock("@apme/ui-workflow", () => ({
  toPrFilesDiffUrl: (url: string) => `${url}/files`,
}));

describe("ActivityPublishedCell", () => {
  it("renders View PR link when pr_url is set", () => {
    render(
      <ActivityPublishedCell
        pr_url="https://github.com/org/repo/pull/42"
        branch_name="apme/remediate-abc"
      />,
    );
    const link = screen.getByRole("link", { name: /view pr/i });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/org/repo/pull/42/files",
    );
  });

  it("renders branch and short SHA when only branch is set", () => {
    render(
      <ActivityPublishedCell
        branch_name="apme/remediate-abc"
        commit_sha="deadbeef12345678"
      />,
    );
    expect(
      screen.getByText("apme/remediate-abc @ deadbeef"),
    ).toBeInTheDocument();
  });

  it("renders em dash when nothing was published", () => {
    const { container } = render(<ActivityPublishedCell />);
    expect(container.textContent).toBe("—");
  });
});
