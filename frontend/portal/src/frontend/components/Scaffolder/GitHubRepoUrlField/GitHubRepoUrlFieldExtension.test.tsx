import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import { GitHubRepoUrlFieldExtension } from "./GitHubRepoUrlFieldExtension";
import { githubRepoUrlValidation } from "./validation";

describe("APME GitHub URL field", () => {
  it.each(["", ".git"])(
    "stores the Backstage picker format for suffix %s",
    (suffix) => {
      const onChange = jest.fn();
      render(
        <GitHubRepoUrlFieldExtension
          {...({
            onChange,
            formData: "",
            schema: { type: "string" },
            uiSchema: {},
            required: true,
            idSchema: { $id: "repoUrl" },
          } as any)}
        />,
      );
      fireEvent.change(screen.getByLabelText("GitHub repository URL"), {
        target: {
          value: `https://github.com/test-rhaap-portal-3/test-amazon-aws${suffix}`,
        },
      });
      expect(onChange).toHaveBeenLastCalledWith(
        "github.com?owner=test-rhaap-portal-3&repo=test-amazon-aws",
      );
      fireEvent.blur(screen.getByLabelText("GitHub repository URL"));
      expect(screen.getByLabelText("GitHub repository URL")).toHaveValue(
        "https://github.com/test-rhaap-portal-3/test-amazon-aws",
      );
    },
  );
  it("clears the committed value when pasted content is invalid", () => {
    const onChange = jest.fn();
    render(
      <GitHubRepoUrlFieldExtension
        {...({
          onChange,
          formData: "",
          schema: {},
          uiSchema: {},
          idSchema: { $id: "repoUrl" },
        } as any)}
      />,
    );
    fireEvent.change(screen.getByLabelText("GitHub repository URL"), {
      target: { value: "https://gitlab.com/acme/repo" },
    });
    expect(onChange).toHaveBeenLastCalledWith("");
    expect(
      screen.getByText("Only github.com repositories are supported right now."),
    ).toBeInTheDocument();
  });
  it("validates picker values without treating arbitrary hosts as GitHub", () => {
    const addError = jest.fn();
    githubRepoUrlValidation("github.com?owner=acme&repo=repo", { addError });
    expect(addError).not.toHaveBeenCalled();
    githubRepoUrlValidation("https://evil.example?owner=acme&repo=repo", {
      addError,
    });
    expect(addError).toHaveBeenCalled();
  });
});
