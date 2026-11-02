/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { TestApiProvider } from "@backstage/test-utils";
import {
  configApiRef,
  discoveryApiRef,
  fetchApiRef,
} from "@backstage/core-plugin-api";
import { ConfigReader } from "@backstage/config";
import type { Rule } from "../../../common/types";
import { apmeApiRef } from "../../api";
import { ApmeRulesTab } from "./ApmeRulesTab";

const MOCK_RULES: Rule[] = [
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
    enabled: true,
    source: "opa",
    enforced: false,
    hasOverride: true,
  },
];

describe("ApmeRulesTab", () => {
  const getRules = jest.fn();
  const updateRuleConfig = jest.fn();
  const deleteRuleConfig = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    getRules.mockResolvedValue(MOCK_RULES.map((r) => ({ ...r })));
    updateRuleConfig.mockImplementation(async (id: string, body: object) => {
      const rule = MOCK_RULES.find((r) => r.id === id);
      if (!rule) throw new Error("missing");
      return { ...rule, ...body, hasOverride: true };
    });
    deleteRuleConfig.mockResolvedValue(undefined);
  });

  function renderTab(initialEntry = "/") {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <TestApiProvider
          apis={[
            [apmeApiRef, { getRules, updateRuleConfig, deleteRuleConfig }],
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [discoveryApiRef, { getBaseUrl: async () => "http://localhost" }],
            [fetchApiRef, { fetch: jest.fn() }],
          ]}
        >
          <ThemeProvider theme={createTheme()}>
            <ApmeRulesTab />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );
  }

  it("loads and lists rules with summary counts", async () => {
    renderTab();
    expect(await screen.findByText("Rules")).toBeInTheDocument();
    expect(getRules).toHaveBeenCalled();
    expect(screen.getByText("M001")).toBeInTheDocument();
    expect(screen.getByText("D001")).toBeInTheDocument();
    expect(screen.getByText(/registered/i)).toBeInTheDocument();
    expect(screen.getByText(/with overrides/i)).toBeInTheDocument();
  });

  it("filters by search text", async () => {
    renderTab();
    await screen.findByText("M001");
    fireEvent.change(screen.getByPlaceholderText(/rule id or description/i), {
      target: { value: "D001" },
    });
    expect(screen.queryByText("M001")).not.toBeInTheDocument();
    expect(screen.getByText("D001")).toBeInTheDocument();
  });

  it("toggles enabled and calls updateRuleConfig", async () => {
    renderTab();
    await screen.findByText("M001");
    const enableSwitch = screen.getByRole("checkbox", {
      name: /enable M001/i,
    });
    fireEvent.click(enableSwitch);
    await waitFor(() => {
      expect(updateRuleConfig).toHaveBeenCalledWith("M001", {
        enabled_override: false,
      });
    });
  });

  it("surfaces object-shaped mutation failures", async () => {
    updateRuleConfig.mockRejectedValueOnce({ message: "Gateway timeout" });
    renderTab();
    await screen.findByText("M001");
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /enable M001/i,
      }),
    );
    expect(await screen.findByText("Gateway timeout")).toBeInTheDocument();
  });

  it("surfaces an error when rule mutation fails", async () => {
    updateRuleConfig.mockRejectedValueOnce(
      new Error("APME API error: 403 - Forbidden"),
    );
    renderTab();
    await screen.findByText("M001");
    const enableSwitch = screen.getByRole("checkbox", {
      name: /enable M001/i,
    });
    fireEvent.click(enableSwitch);
    expect(
      await screen.findByText(/APME API error: 403 - Forbidden/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "APME API error: 403 - Forbidden",
    );
  });

  it("resets override via deleteRuleConfig", async () => {
    renderTab();
    await screen.findByText("D001");
    const resetButtons = screen.getAllByRole("button", { name: /^reset$/i });
    fireEvent.click(resetButtons[0]);
    await waitFor(() => {
      expect(deleteRuleConfig).toHaveBeenCalledWith("D001");
    });
    expect(getRules).toHaveBeenCalledTimes(2);
  });

  it("opens the rule detail dialog from ?rule= deep link", async () => {
    renderTab("/?rule=M001");
    expect(await screen.findByText(/Rule: M001/i)).toBeInTheDocument();
  });

  it("opens the rule detail dialog when catalog ids use the native: prefix", async () => {
    getRules.mockResolvedValueOnce([
      {
        ...MOCK_RULES[0],
        id: "native:L001",
        name: "yaml-syntax",
        description: "YAML syntax check",
      },
    ]);
    renderTab("/?rule=L001");
    expect(await screen.findByText(/Rule: native:L001/i)).toBeInTheDocument();
  });
});
