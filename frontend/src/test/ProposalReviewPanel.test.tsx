import { describe, it, expect, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProposalReviewPanel } from "@apme/ui-workflow";
import type { OperationProposal } from "@apme/ui-workflow";

const nodeProposals: OperationProposal[] = [
  {
    id: "t1-0000",
    rule_id: "L026,M001",
    file: "playbook.yml",
    path: "playbook.yml::tasks[0]",
    source: "deterministic",
    tier: 1,
    confidence: 1,
    diff_hunk: "@@ -1 +1 @@\n-old\n+new",
    status: "proposed",
  },
  {
    id: "t1-0001",
    rule_id: "L001",
    file: "other.yml",
    path: "other.yml::tasks[1]",
    source: "deterministic",
    tier: 1,
    confidence: 1,
    diff_hunk: "@@ -1 +1 @@\n-a\n+b",
    status: "proposed",
  },
];

describe("ProposalReviewPanel", () => {
  it("renders per-node cards from path and multi-rule chips", () => {
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={vi.fn()} />,
    );
    expect(screen.getByText("playbook.yml::tasks[0]")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "L026" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "M001" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Expand all/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Collapse all/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();
  });

  it("Next sends only accepted proposal ids", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={onApprove} />,
    );

    const acceptButtons = screen.getAllByRole("radio", { name: /^Accept$/i });
    const declineButtons = screen.getAllByRole("radio", { name: /^Decline$/i });
    await user.click(acceptButtons[0]!);
    await user.click(declineButtons[1]!);
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onApprove).toHaveBeenCalledWith(["t1-0000"]);
  });

  it("Decline remaining then Next sends empty approved_ids", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={onApprove} />,
    );
    await user.click(
      screen.getByRole("button", { name: /Decline remaining/i }),
    );
    expect(screen.getByText("playbook.yml::tasks[0]")).toBeInTheDocument();
    expect(screen.getByText("other.yml::tasks[1]")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onApprove).toHaveBeenCalledWith([]);
  });

  it("keeps declined cards visible when draft status becomes declined", () => {
    const declinedDraft = nodeProposals.map((p) => ({
      ...p,
      status: "declined" as const,
    }));
    render(
      <ProposalReviewPanel proposals={declinedDraft} onApprove={vi.fn()} />,
    );
    expect(screen.getByText("playbook.yml::tasks[0]")).toBeInTheDocument();
    expect(screen.getByText("other.yml::tasks[1]")).toBeInTheDocument();
    expect(screen.queryByText(/Declined by AI/i)).not.toBeInTheDocument();
  });

  it("Accept remaining only fills undecided nodes", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={onApprove} />,
    );
    const declineButtons = screen.getAllByRole("radio", { name: /^Decline$/i });
    await user.click(declineButtons[0]!);
    await user.click(screen.getByRole("button", { name: /Accept remaining/i }));
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onApprove).toHaveBeenCalledWith(["t1-0001"]);
  });

  it("enables Next when Gate 2 has only AI could-not-fix rows", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const declinedOnly: OperationProposal[] = [
      {
        id: "ai-0000",
        rule_id: "L005",
        file: "playbook-1057-wrong-module.yml",
        path: "playbook-1057-wrong-module.yml/plays[0]/tasks[0]",
        source: "ai",
        tier: 2,
        confidence: 0,
        status: "declined",
        explanation: "AI could not generate a fix for this violation.",
        suggestion: "Use a validated collection instead.",
      },
    ];
    render(
      <ProposalReviewPanel
        proposals={declinedOnly}
        onApprove={onApprove}
        reviewGate="ai"
        pastAiEscalation
      />,
    );
    expect(
      screen.getByText(/Continue with no AI fixes applied/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onApprove).toHaveBeenCalledWith([]);
  });

  it("Clear resets decisions and disables Next again", async () => {
    const user = userEvent.setup();
    const onDraftUpdate = vi.fn();
    render(
      <ProposalReviewPanel
        proposals={nodeProposals}
        onApprove={vi.fn()}
        onDraftUpdate={onDraftUpdate}
      />,
    );
    expect(screen.getByRole("button", { name: /^Clear$/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Accept remaining/i }));
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /^Clear$/i }));
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Clear$/i })).toBeDisabled();
  });
  it("syncs per-location draft decisions and retains exclusive selection", async () => {
    const user = userEvent.setup();
    const onDraftUpdate = vi.fn();
    render(
      <ProposalReviewPanel
        proposals={nodeProposals}
        onApprove={vi.fn()}
        onDraftUpdate={onDraftUpdate}
      />,
    );
    const accepts = screen.getAllByRole("radio", { name: /^Accept$/i });
    const declines = screen.getAllByRole("radio", { name: /^Decline$/i });
    await user.click(accepts[0]!);
    expect(accepts[0]).toBeChecked();
    expect(declines[0]).not.toBeChecked();
    await waitFor(() =>
      expect(onDraftUpdate).toHaveBeenLastCalledWith([
        { proposal_id: "t1-0000", status: "approved" },
        { proposal_id: "t1-0001", status: "pending" },
      ]),
    );
    act(() => declines[0]!.focus());
    await user.keyboard(" ");
    expect(declines[0]).toBeChecked();
    expect(accepts[0]).not.toBeChecked();
    await waitFor(() =>
      expect(onDraftUpdate).toHaveBeenLastCalledWith([
        { proposal_id: "t1-0000", status: "declined" },
        { proposal_id: "t1-0001", status: "pending" },
      ]),
    );
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();
  });
  it("exposes an independent keyboard expander beside the decision group", async () => {
    const user = userEvent.setup();
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={vi.fn()} />,
    );
    const expander = screen.getByRole("button", {
      name: "Collapse playbook.yml::tasks[0]",
    });
    expect(expander).toHaveAttribute("aria-expanded", "true");
    expect(
      screen
        .getAllByRole("radio", { name: "Accept" })[0]!
        .closest('[role="button"]'),
    ).toBeNull();
    act(() => expander.focus());
    await user.keyboard(" ");
    expect(expander).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getAllByRole("radio", { name: "Accept" })[0],
    ).not.toBeChecked();
  });
});
