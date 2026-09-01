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

  it("resolveRuleHref renders rule definition links instead of filter chips", () => {
    render(
      <AssessFindingsPanel
        findings={findings}
        resolveRuleHref={(bareId) =>
          bareId === "L050"
            ? "/self-service/repositories/quality-settings?rule=L050"
            : undefined
        }
      />,
    );

    const l050Links = screen.getAllByRole("link", {
      name: "View rule definition: L050",
    });
    expect(l050Links).toHaveLength(2);
    for (const link of l050Links) {
      expect(link).toHaveAttribute(
        "href",
        "/self-service/repositories/quality-settings?rule=L050",
      );
    }
    expect(
      screen.queryByRole("button", { name: "L050" }),
    ).not.toBeInTheDocument();
  });

  it("ruleHrefTarget=_blank opens rule definition links in a new tab", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const user = userEvent.setup();
    render(
      <AssessFindingsPanel
        findings={findings}
        resolveRuleHref={(bareId) =>
          bareId === "L050"
            ? "/self-service/repositories/quality-settings?rule=L050"
            : undefined
        }
        ruleHrefTarget="_blank"
      />,
    );

    const l050Links = screen.getAllByRole("link", {
      name: "View rule definition in new tab: L050",
    });
    expect(l050Links).toHaveLength(2);
    for (const link of l050Links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }

    await user.click(l050Links[0]!);
    expect(openSpy).toHaveBeenCalledWith(
      "/self-service/repositories/quality-settings?rule=L050",
      "_blank",
      "noopener,noreferrer",
    );
    openSpy.mockRestore();
  });

  it("resolveRuleHref keeps filter chips for rules without a catalog href", async () => {
    const user = userEvent.setup();
    render(
      <AssessFindingsPanel
        findings={findings}
        resolveRuleHref={(bareId) =>
          bareId === "L050"
            ? "/self-service/repositories/quality-settings?rule=L050"
            : undefined
        }
      />,
    );

    const m001Buttons = screen.getAllByRole("button", { name: "M001" });
    expect(m001Buttons.length).toBeGreaterThan(0);
    await user.click(m001Buttons[0]!);
    expect(
      screen.getByLabelText("Selected rule filters"),
    ).toHaveTextContent("M001");
  });
});
