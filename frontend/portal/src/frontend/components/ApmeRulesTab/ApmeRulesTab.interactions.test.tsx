/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { mockApis, TestApiProvider } from "@backstage/test-utils";
import { errorApiRef } from "@backstage/core-plugin-api";
import { translationApiRef } from "@backstage/core-plugin-api/alpha";
import type { Rule } from "../../../common/types";
import { apmeApiRef } from "../../api";
import { ApmeRulesTab } from "./ApmeRulesTab";

const rules: Rule[] = [
  {
    id: "M001",
    name: "fqcn-builtins",
    description: "Use fully qualified collection names",
    severity: "high",
    defaultSeverity: "medium",
    category: "lint",
    remediationClass: 1,
    enabled: true,
    source: "native",
    enforced: false,
    hasOverride: false,
  },
  {
    id: "D001",
    name: "pinned-dep",
    description: "Pin collection versions",
    severity: "medium",
    category: "dependencies",
    remediationClass: 3,
    enabled: false,
    source: "opa",
    enforced: true,
    hasOverride: true,
  },
];

function LocationSearch() {
  return <output aria-label="Location search">{useLocation().search}</output>;
}

describe("ApmeRulesTab interactions", () => {
  const getRules = jest.fn();
  const updateRuleConfig = jest.fn();
  const deleteRuleConfig = jest.fn();

  beforeEach(() => {
    jest.resetAllMocks();
    getRules.mockResolvedValue(rules.map((rule) => ({ ...rule })));
    updateRuleConfig.mockResolvedValue(undefined);
    deleteRuleConfig.mockResolvedValue(undefined);
  });

  function renderTab(initialEntry = "/") {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <TestApiProvider
          apis={[
            [apmeApiRef, { getRules, updateRuleConfig, deleteRuleConfig }],
            [errorApiRef, { post: jest.fn() }],
            [translationApiRef, mockApis.translation()],
          ]}
        >
          <ThemeProvider theme={createTheme()}>
            <ApmeRulesTab />
            <LocationSearch />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );
  }

  function selectOption(label: string, option: string) {
    fireEvent.mouseDown(
      screen.getByLabelText(label, { selector: '[role="button"]' }),
    );
    fireEvent.click(screen.getByRole("option", { name: option }));
  }

  it("combines category and source filters and restores all rules", async () => {
    renderTab();
    await screen.findByText("M001");
    selectOption("Category", "Dependencies");
    expect(screen.queryByText("M001")).not.toBeInTheDocument();
    expect(screen.getByText("D001")).toBeInTheDocument();
    expect(screen.getByText("1 rule shown")).toBeInTheDocument();
    selectOption("Source", "native");
    expect(
      screen.getByText("No rules match the current filters."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    selectOption("Category", "All categories");
    expect(screen.getByText("M001")).toBeInTheDocument();
    expect(screen.queryByText("D001")).not.toBeInTheDocument();
    selectOption("Source", "All sources");
    expect(screen.getByText("2 rules shown")).toBeInTheDocument();
    expect(screen.getByText("D001")).toBeInTheDocument();
  });

  it.each(["  COLLECTION NAMES  ", "FQCN-BUILTINS"])(
    "searches descriptions and names ignoring case and whitespace: %s",
    async (query) => {
      renderTab();
      await screen.findByText("M001");
      fireEvent.change(screen.getByPlaceholderText("Rule ID or description"), {
        target: { value: query },
      });
      expect(screen.getByText("M001")).toBeInTheDocument();
      expect(screen.queryByText("D001")).not.toBeInTheDocument();
    },
  );

  it("shows the empty catalog after loading", async () => {
    getRules.mockResolvedValueOnce([]);
    renderTab();
    expect(
      await screen.findByText(/No rules in the catalog yet/),
    ).toBeInTheDocument();
    expect(screen.getByText("0 rules shown")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows friendly guidance when the gateway cannot be reached", async () => {
    getRules.mockRejectedValueOnce(new Error("fetch failed: ECONNREFUSED"));
    renderTab();
    expect(await screen.findByText("APME unavailable")).toBeInTheDocument();
    expect(
      screen.getByText(/Check that the modernization service is running/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it.each([new Error("Catalog access denied"), "Catalog access denied"])(
    "surfaces catalog load errors: %s",
    async (error) => {
      getRules.mockRejectedValueOnce(error);
      renderTab();
      expect(
        await screen.findByText("Catalog access denied"),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    },
  );

  it("updates severity in the table and leaves other rules unchanged", async () => {
    renderTab();
    await screen.findByText("M001");
    selectOption("Severity for M001", "Critical");
    await waitFor(() =>
      expect(updateRuleConfig).toHaveBeenCalledWith("M001", {
        severity_override: 6,
      }),
    );
    expect(screen.getByLabelText("Severity for M001")).toHaveTextContent(
      "Critical",
    );
    expect(screen.getByLabelText("Severity for D001")).toHaveTextContent(
      "Medium",
    );
    expect(
      within(
        screen.getByRole("button", { name: "M001" }).closest("tr")!,
      ).getByRole("button", { name: "Reset" }),
    ).toBeEnabled();
  });

  it("restores severity and the override flag after a failed mutation", async () => {
    updateRuleConfig.mockRejectedValueOnce("Severity change forbidden");
    renderTab();
    await screen.findByText("M001");
    selectOption("Severity for M001", "Low");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Severity change forbidden",
    );
    expect(screen.getByLabelText("Severity for M001")).toHaveTextContent(
      "High",
    );
    expect(
      within(
        screen.getByRole("button", { name: "M001" }).closest("tr")!,
      ).queryByRole("button", { name: "Reset" }),
    ).not.toBeInTheDocument();
  });

  it("can remove enforcement from a disabled rule", async () => {
    renderTab();
    await screen.findByText("D001");
    expect(
      screen.getByRole("checkbox", { name: "Enable D001" }),
    ).not.toBeChecked();
    const enforce = screen.getByRole("checkbox", { name: "Enforce D001" });
    expect(enforce).toBeChecked();
    fireEvent.click(enforce);
    await waitFor(() =>
      expect(updateRuleConfig).toHaveBeenCalledWith("D001", {
        enforced: false,
      }),
    );
    expect(enforce).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Enable D001" }),
    ).not.toBeChecked();
  });

  it("enables a disabled rule without removing its enforcement", async () => {
    renderTab();
    await screen.findByText("D001");
    const enable = screen.getByRole("checkbox", { name: "Enable D001" });
    fireEvent.click(enable);
    await waitFor(() => expect(enable).toBeEnabled());
    expect(updateRuleConfig).toHaveBeenCalledWith("D001", {
      enabled_override: true,
    });
    expect(enable).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Enforce D001" }),
    ).toBeChecked();
  });

  it("restores the selected rule severity after a failed dialog mutation", async () => {
    updateRuleConfig.mockRejectedValueOnce(new Error("Severity forbidden"));
    renderTab("/?rule=M001");
    const dialog = await screen.findByRole("dialog");
    selectOption("Override severity for M001", "Low");
    await waitFor(() =>
      expect(
        within(dialog).getByLabelText("Override severity for M001"),
      ).toHaveTextContent("High"),
    );
    expect(updateRuleConfig).toHaveBeenCalledWith("M001", {
      severity_override: 2,
    });
    expect(
      within(dialog).queryByRole("button", { name: "Reset override" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close rule detail" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Severity forbidden");
    expect(screen.getByLabelText("Severity for M001")).toHaveTextContent(
      "High",
    );
  });

  it("rolls back failed enforcement and clears the error on a successful retry", async () => {
    updateRuleConfig.mockRejectedValueOnce(new Error("Enforcement forbidden"));
    renderTab();
    await screen.findByText("M001");
    const enforce = screen.getByRole("checkbox", { name: "Enforce M001" });
    fireEvent.click(enforce);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enforcement forbidden",
    );
    expect(enforce).not.toBeChecked();
    fireEvent.click(enforce);
    await waitFor(() => expect(enforce).toBeEnabled());
    expect(enforce).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(updateRuleConfig).toHaveBeenLastCalledWith("M001", {
      enforced: true,
    });
  });

  it("keeps dialog and table controls synchronized and disables them while saving", async () => {
    let finishSave!: () => void;
    updateRuleConfig.mockReturnValueOnce(
      new Promise<void>((resolve) => {
        finishSave = resolve;
      }),
    );
    renderTab();
    await screen.findByText("M001");
    fireEvent.click(screen.getByRole("button", { name: "M001" }));
    const dialog = screen.getByRole("dialog");
    const enable = within(dialog).getByRole("checkbox", {
      name: "Enable M001",
    });
    fireEvent.click(enable);
    expect(enable).not.toBeChecked();
    expect(enable).toBeDisabled();
    expect(
      within(dialog).getByRole("checkbox", { name: "Enforce M001" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByLabelText("Override severity for M001"),
    ).toHaveAttribute("aria-disabled", "true");
    expect(
      within(dialog).getByRole("button", { name: "Reset override" }),
    ).toBeDisabled();
    await act(async () => finishSave());
    expect(enable).toBeEnabled();
    expect(updateRuleConfig).toHaveBeenCalledWith("M001", {
      enabled_override: false,
    });
    fireEvent.click(
      within(dialog).getByRole("checkbox", { name: "Enforce M001" }),
    );
    await waitFor(() =>
      expect(
        within(dialog).getByRole("checkbox", { name: "Enforce M001" }),
      ).toBeEnabled(),
    );
    selectOption("Override severity for M001", "Info");
    await waitFor(() =>
      expect(updateRuleConfig).toHaveBeenLastCalledWith("M001", {
        severity_override: 1,
      }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("checkbox", { name: "Enable M001" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Enforce M001" }),
    ).toBeChecked();
    expect(screen.getByLabelText("Severity for M001")).toHaveTextContent(
      "Info",
    );
  });

  it("resets an override from the dialog, reloads defaults and closes it", async () => {
    getRules
      .mockResolvedValueOnce(rules.map((rule) => ({ ...rule })))
      .mockResolvedValueOnce([
        { ...rules[1], hasOverride: false, enforced: false },
      ]);
    renderTab("/?rule=D001&tab=rules");
    const dialog = await screen.findByRole("dialog");
    expect(screen.getByLabelText("Location search")).toHaveTextContent(
      "?tab=rules",
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Reset override" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(deleteRuleConfig).toHaveBeenCalledWith("D001");
    expect(getRules).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("checkbox", { name: "Enforce D001" }),
    ).not.toBeChecked();
    expect(
      screen.queryByRole("button", { name: "Reset" }),
    ).not.toBeInTheDocument();
  });

  it.each(["APME API error: 404 - missing override", "Override not found"])(
    "allows retrying reset after a harmless missing override: %s",
    async (error) => {
      deleteRuleConfig.mockRejectedValueOnce(error);
      renderTab();
      await screen.findByText("D001");
      fireEvent.click(screen.getByRole("button", { name: "Reset" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Reset" })).toBeEnabled(),
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(getRules).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Reset" }));
      await waitFor(() => expect(getRules).toHaveBeenCalledTimes(2));
      expect(deleteRuleConfig).toHaveBeenCalledTimes(2);
    },
  );

  it("preserves the override and shows a reset permission failure", async () => {
    deleteRuleConfig.mockRejectedValueOnce(new Error("403 - Reset forbidden"));
    renderTab("/?rule=D001");
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Reset override" }),
    );
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Reset override" }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close rule detail" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "403 - Reset forbidden",
    );
    expect(getRules).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Reset" })).toBeEnabled();
  });

  it("ignores an unknown deep link and lets the user open and dismiss a valid rule", async () => {
    renderTab("/?rule=missing&tab=rules");
    await screen.findByText("M001");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Location search")).toHaveTextContent(
      "?rule=missing&tab=rules",
    );
    fireEvent.click(screen.getByRole("button", { name: "M001" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).queryByRole("button", { name: "Reset override" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape", code: "Escape", keyCode: 27 });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("renders missing description and source in both the table and dialog", async () => {
    getRules.mockResolvedValueOnce([
      { ...rules[0], description: "", source: undefined },
    ]);
    renderTab();
    await screen.findByText("M001");
    expect(within(screen.getByRole("table")).getAllByText("—")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "M001" }));
    expect(within(screen.getByRole("dialog")).getAllByText("—")).toHaveLength(
      2,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close rule detail" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});
