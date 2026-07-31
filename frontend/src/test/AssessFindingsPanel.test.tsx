import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssessFindingsPanel } from "@apme/ui-workflow";
import type { AssessFinding } from "@apme/ui-workflow";

const findings: AssessFinding[] = [
  {
    rule_id: "native:L050",
    severity: "high",
    message: "needs FQCN",
    file: "play.yml",
    path: "play.yml/plays[0]/tasks[0]",
    node_type: "task",
    remediation_class: 1,
    original_yaml: "- name: a\n  debug:\n    msg: hi\n",
  },
  {
    rule_id: "M001",
    severity: "medium",
    message: "legacy module",
    file: "play.yml",
    path: "play.yml/plays[0]/tasks[1]",
    node_type: "task",
    remediation_class: 2,
    original_yaml: "- name: b\n  debug:\n    msg: bye\n",
  },
  {
    rule_id: "L050,M001",
    severity: "low",
    message: "coupled",
    file: "roles/x/tasks/main.yml",
    path: "roles/x/tasks/main.yml/tasks[0]",
    node_type: "task",
    remediation_class: 3,
  },
];

describe("AssessFindingsPanel rule filter", () => {
  it("filters to selected rule via typeahead and shows count title", async () => {
    const user = userEvent.setup();
    render(<AssessFindingsPanel findings={findings} />);

    await user.click(screen.getByLabelText("Filter by rule ID"));
    const combobox = screen.getByRole("combobox");
    await user.type(combobox, "L050");
    await user.click(within(await screen.findByRole("listbox")).getByText("L050"));

    expect(screen.getByText(/Showing 2 findings of 3/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("L050");
  });

  it("OR-filters when multiple rules are selected", async () => {
    const user = userEvent.setup();
    render(<AssessFindingsPanel findings={findings} />);

    await user.click(screen.getByLabelText("Filter by rule ID"));
    await user.type(screen.getByRole("combobox"), "L050");
    await user.click(within(await screen.findByRole("listbox")).getByText("L050"));

    await user.click(screen.getByLabelText("Filter by rule ID"));
    await user.type(screen.getByRole("combobox"), "M001");
    await user.click(within(await screen.findByRole("listbox")).getByText("M001"));

    // All three findings match L050 or M001.
    expect(screen.getByText(/Showing 3 findings of 3/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("L050");
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("M001");
  });

  it("clicking a RuleId chip toggles that rule into the filter", async () => {
    const user = userEvent.setup();
    render(<AssessFindingsPanel findings={findings} />);

    // defaultExpanded — rule chips are already visible.
    const ruleChips = screen.getAllByRole("button", { name: "L050" });
    await user.click(ruleChips[0]!);

    expect(screen.getByText(/Showing 2 findings of 3/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("L050");
  });

  it("initialRuleFilters seeds the Rule filter from the host", () => {
    render(
      <AssessFindingsPanel
        findings={findings}
        initialRuleFilters={["native:L050"]}
      />,
    );

    expect(screen.getByText(/Showing 2 findings of 3/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("L050");
  });
});
