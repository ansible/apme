/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import type { Violation } from "../../../common/types";
import {
  ViolationDetailModal,
  ViolationDetailModalProps,
} from "./ViolationDetailModal";

function violation(id: number, overrides: Partial<Violation> = {}): Violation {
  return {
    id,
    rule_id: `L00${id}`,
    level: "high",
    message: `Finding ${id}`,
    file: "playbook.yml",
    line: 12,
    remediation_class: 1,
    validator_source: "native",
    ...overrides,
  };
}

function modal(props: Partial<ViolationDetailModalProps> = {}) {
  return (
    <ThemeProvider theme={createTheme()}>
      <ViolationDetailModal
        open
        title="Lint findings"
        violations={[violation(1)]}
        onClose={jest.fn()}
        {...props}
      />
    </ThemeProvider>
  );
}

function row(id: number): HTMLElement {
  return screen
    .getByText(`Finding ${id}`)
    .closest("[data-violation-id]") as HTMLElement;
}

describe("ViolationDetailModal", () => {
  it("shows finding metadata and closes using its close control or Escape", () => {
    const onClose = jest.fn();
    render(modal({ subtitle: "demo repository", onClose }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Lint findings");
    expect(screen.getByText("demo repository")).toBeInTheDocument();
    expect(screen.getByText("L001")).toBeInTheDocument();
    expect(screen.getByText(/playbook.yml:12/)).toBeInTheDocument();
    expect(screen.getByText(/high/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /acknowledge/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(screen.getByRole("dialog"), {
      key: "Escape",
      keyCode: 27,
    });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("sorts open findings before acknowledged findings, then by ID without mutating input", () => {
    const violations = [
      violation(4, { suppressed: true }),
      violation(3),
      violation(1, { suppressed: true }),
      violation(2),
    ];
    render(modal({ violations, onAcknowledge: jest.fn() }));
    expect(
      screen.getAllByText(/^Finding /).map((node) => node.textContent),
    ).toEqual(["Finding 2", "Finding 3", "Finding 1", "Finding 4"]);
    expect(violations.map((v) => v.id)).toEqual([4, 3, 1, 2]);
    expect(
      within(row(1)).getByRole("button", { name: "Acknowledged" }),
    ).toBeEnabled();
    expect(
      within(row(2)).getByRole("button", { name: "Acknowledge" }),
    ).toBeEnabled();
  });

  it("uses a supplied acknowledgment predicate for ordering and toggle callbacks", () => {
    const onAcknowledge = jest.fn().mockResolvedValue(undefined);
    const onUnacknowledge = jest.fn().mockResolvedValue(undefined);
    const first = violation(1, { suppressed: true });
    const second = violation(2);
    render(
      modal({
        violations: [first, second],
        isAcknowledged: (v) => v.id === 2,
        onAcknowledge,
        onUnacknowledge,
      }),
    );
    expect(
      screen.getAllByText(/^Finding /).map((node) => node.textContent),
    ).toEqual(["Finding 1", "Finding 2"]);
    fireEvent.click(
      within(row(1)).getByRole("button", { name: "Acknowledge" }),
    );
    fireEvent.click(
      within(row(2)).getByRole("button", { name: "Acknowledged" }),
    );
    expect(onAcknowledge).toHaveBeenCalledTimes(1);
    expect(onAcknowledge).toHaveBeenCalledWith(first);
    expect(onUnacknowledge).toHaveBeenCalledTimes(1);
    expect(onUnacknowledge).toHaveBeenCalledWith(second);
  });

  it("disables only the pending row and preserves its scroll position when findings reorder", () => {
    const onAcknowledge = jest.fn().mockResolvedValue(undefined);
    const first = violation(1);
    const second = violation(2);
    const { rerender } = render(
      modal({ violations: [first, second], onAcknowledge }),
    );
    const scrollIntoView = jest.fn();
    row(1).scrollIntoView = scrollIntoView;
    fireEvent.click(
      within(row(1)).getByRole("button", { name: "Acknowledge" }),
    );
    rerender(
      modal({ violations: [first, second], onAcknowledge, acknowledgingId: 1 }),
    );
    expect(
      within(row(1)).getByRole("button", { name: "Saving…" }),
    ).toBeDisabled();
    expect(
      within(row(2)).getByRole("button", { name: "Acknowledge" }),
    ).toBeEnabled();
    fireEvent.click(within(row(1)).getByRole("button", { name: "Saving…" }));
    expect(onAcknowledge).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    rerender(
      modal({
        violations: [{ ...first, suppressed: true }, second],
        onAcknowledge,
      }),
    );
    expect(
      within(row(1)).getByRole("button", { name: "Acknowledged" }),
    ).toBeEnabled();
    expect(
      screen.getAllByText(/^Finding /).map((node) => node.textContent),
    ).toEqual(["Finding 2", "Finding 1"]);
  });

  it.each(["acknowledge", "unacknowledge"] as const)(
    "handles a missing %s callback without calling the opposite action",
    (action) => {
      const callback = jest.fn().mockResolvedValue(undefined);
      const props =
        action === "acknowledge"
          ? { onUnacknowledge: callback, violations: [violation(1)] }
          : {
              onAcknowledge: callback,
              violations: [violation(1, { suppressed: true })],
            };
      render(modal(props));
      fireEvent.click(within(row(1)).getByRole("button"));
      expect(callback).not.toHaveBeenCalled();
    },
  );

  it("renders collection paths only for collection health and omits missing file/line data", () => {
    render(
      modal({
        violations: [
          violation(1, {
            file: "",
            line: 0,
            path: "community.general",
            validator_source: "collection_health",
          }),
          violation(2, { path: "internal.task.path" }),
        ],
      }),
    );
    expect(
      within(row(1)).getByText(/— · community.general/),
    ).toBeInTheDocument();
    expect(within(row(2)).getByText(/playbook.yml:12/)).toBeInTheDocument();
    expect(screen.queryByText(/internal.task.path/)).not.toBeInTheDocument();
  });

  it.each([
    {
      original_yaml: "shell: echo old",
      fixed_yaml: "ansible.builtin.command: echo new",
      expected: ["shell: echo old", "ansible.builtin.command: echo new"],
      prefixes: ["−", "+"],
    },
    {
      original_yaml: "debug: unchanged",
      expected: ["debug: unchanged"],
      prefixes: [],
    },
    {
      fixed_yaml: "ansible.builtin.debug: added",
      expected: ["ansible.builtin.debug: added"],
      prefixes: ["+"],
    },
    {
      original_yaml: "shell: removed",
      fixed_yaml: "",
      expected: ["shell: removed"],
      prefixes: ["−"],
    },
  ])(
    "shows real YAML diff with optional content: $expected",
    ({ expected, prefixes, ...yaml }) => {
      render(modal({ violations: [violation(1, yaml)] }));
      for (const text of expected)
        expect(within(row(1)).getByText(text)).toBeInTheDocument();
      for (const prefix of prefixes)
        expect(within(row(1)).getByText(prefix)).toBeInTheDocument();
    },
  );

  it("shows the empty state without subtitle or action controls", () => {
    render(modal({ violations: [], onAcknowledge: jest.fn() }));
    expect(screen.getByText("No violations to display.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /acknowledge/i }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("does not display content while closed", () => {
    render(modal({ open: false }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Finding 1")).not.toBeInTheDocument();
  });
});
