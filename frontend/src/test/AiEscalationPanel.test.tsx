import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AiEscalationPanel } from "../components/AiEscalationPanel";
import type { AssessFinding } from "../hooks/useProjectOperationState";

const candidates: AssessFinding[] = [
  {
    rule_id: "L050",
    severity: "warning",
    message: "needs AI",
    file: "play.yml",
    path: "play.yml/plays[0]/tasks[0]",
    node_type: "task",
    original_yaml: "- name: a\n  debug:\n    msg: hi\n",
  },
  {
    rule_id: "L051",
    severity: "info",
    message: "also AI",
    file: "play.yml",
    path: "play.yml/plays[0]/tasks[1]",
    node_type: "task",
    original_yaml: "- name: b\n  debug:\n    msg: bye\n",
  },
];

describe("AiEscalationPanel", () => {
  it("disables Next until every location is Include or Skip", async () => {
    const user = userEvent.setup();
    const onEscalate = vi.fn();
    render(
      <AiEscalationPanel candidates={candidates} onEscalate={onEscalate} />,
    );

    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();

    const includeButtons = screen.getAllByRole("button", { name: /^Include$/i });
    await user.click(includeButtons[0]!);
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();

    await user.click(includeButtons[1]!);
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeEnabled();
  });

  it("Next with all Skip sends empty targets (skip AI)", async () => {
    const user = userEvent.setup();
    const onEscalate = vi.fn();
    render(
      <AiEscalationPanel candidates={candidates} onEscalate={onEscalate} />,
    );

    await user.click(screen.getByRole("button", { name: /Skip remaining/i }));
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onEscalate).toHaveBeenCalledWith([]);
  });

  it("Next with Include sends path targets with empty rule_ids", async () => {
    const user = userEvent.setup();
    const onEscalate = vi.fn();
    render(
      <AiEscalationPanel candidates={candidates} onEscalate={onEscalate} />,
    );

    await user.click(
      screen.getByRole("button", { name: /Include remaining/i }),
    );
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onEscalate).toHaveBeenCalledWith([
      { path: "play.yml/plays[0]/tasks[0]", rule_ids: [] },
      { path: "play.yml/plays[0]/tasks[1]", rule_ids: [] },
    ]);
  });
});
