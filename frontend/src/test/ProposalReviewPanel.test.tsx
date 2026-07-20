import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProposalReviewPanel } from "../components/ProposalReviewPanel";
import type { OperationProposal } from "../types/operation";

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
    expect(screen.getByText("L026")).toBeInTheDocument();
    expect(screen.getByText("M001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Expand all/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Collapse all/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Next$/i })).toBeDisabled();
  });

  it("Next sends only accepted proposal ids", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(
      <ProposalReviewPanel proposals={nodeProposals} onApprove={onApprove} />,
    );

    const acceptButtons = screen.getAllByRole("button", { name: /^Accept$/i });
    const declineButtons = screen.getAllByRole("button", { name: /^Decline$/i });
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
    await user.click(screen.getByRole("button", { name: /Decline remaining/i }));
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
    const declineButtons = screen.getAllByRole("button", { name: /^Decline$/i });
    await user.click(declineButtons[0]!);
    await user.click(screen.getByRole("button", { name: /Accept remaining/i }));
    await user.click(screen.getByRole("button", { name: /^Next$/i }));
    expect(onApprove).toHaveBeenCalledWith(["t1-0001"]);
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
});
