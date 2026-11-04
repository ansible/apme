import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import type { Entity } from "@backstage/catalog-model";
import { NativeEntityQualityTab } from "./NativeEntityQualityTab";

jest.mock("./index", () => ({
  EntityQualityTab: ({
    initialRuleFilter,
    initialCategoryFilter,
  }: {
    initialRuleFilter?: string;
    initialCategoryFilter?: string;
  }) => (
    <div data-testid="filters">
      {initialRuleFilter ?? ""}|{initialCategoryFilter ?? ""}
    </div>
  ),
}));

function UpdateSearch() {
  const [, setSearchParams] = useSearchParams();
  return (
    <button
      type="button"
      onClick={() =>
        setSearchParams({ rule: "R002", category: "security" })
      }
    >
      Update filters
    </button>
  );
}

describe("NativeEntityQualityTab URL filters", () => {
  it("updates filters when the route query changes after render", () => {
    const entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: { name: "repo" },
      spec: { type: "git-repository" },
    } as Entity;
    render(
      <MemoryRouter initialEntries={["/?rule=R001&category=reliability"]}>
        <NativeEntityQualityTab entity={entity} />
        <UpdateSearch />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("filters")).toHaveTextContent(
      "R001|reliability",
    );
    fireEvent.click(screen.getByRole("button", { name: "Update filters" }));
    expect(screen.getByTestId("filters")).toHaveTextContent("R002|security");
  });
});
