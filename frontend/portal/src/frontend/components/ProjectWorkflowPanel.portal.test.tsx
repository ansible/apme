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
} from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { MemoryRouter } from "react-router-dom";
import type {
  ProjectOperationState,
  ProjectWorkflowController,
} from "@apme/ui-workflow";
import { ProjectWorkflowPanel } from "@apme/ui-workflow";

// Exercise the shared source, including OperationPanel and both shipping panels,
// without requiring a previously packed workflow artifact.
jest.mock("@apme/ui-workflow", () =>
  jest.requireActual(
    "../../../../packages/ui-workflow/src/ProjectWorkflowPanel",
  ),
);

// Draft persistence is owned by ProjectWorkflowPanel; a small review surface
// lets these tests trigger it without coupling to proposal editor internals.
jest.mock(
  "../../../../packages/ui-workflow/src/components/ProposalReviewPanel",
  () => ({
    ProposalReviewPanel: ({
      onDraftUpdate,
    }: {
      onDraftUpdate: (updates: []) => void;
    }) => <button onClick={() => onDraftUpdate([])}>Save draft</button>,
  }),
);

// React Aria measures controls in the browser; jsdom has no layout observer.
beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

function workflowStub(
  overrides: Partial<ProjectWorkflowController> = {},
): ProjectWorkflowController {
  return {
    attachOp: false,
    setAttachOp: jest.fn(),
    opState: null,
    isRunning: false,
    isCancelling: false,
    operationActive: false,
    sessionTabVisible: false,
    refreshOp: jest.fn(),
    clearOp: jest.fn(),
    startScan: jest.fn(),
    beginRemediate: jest.fn(),
    escalateAi: jest.fn(),
    approve: jest.fn(),
    cancel: jest.fn(),
    createPR: jest.fn(),
    patchProposals: jest.fn().mockResolvedValue(undefined),
    dismiss: jest.fn(),
    resumeSession: jest.fn(),
    startOver: jest.fn(),
    findResumableScanId: jest.fn(),
    ...overrides,
  };
}

function operation(
  overrides: Partial<ProjectOperationState> = {},
): ProjectOperationState {
  return {
    operation_id: "op-1",
    project_id: "proj-1",
    scan_id: "scan-1",
    status: "completed",
    scan_type: "remediate",
    started_at: "2026-01-01T00:00:00Z",
    progress: [],
    result: {
      total_violations: 2,
      fixable: 2,
      manual_review: 0,
      ai_proposed: 0,
      ai_declined: 0,
      ai_accepted: 0,
      remediated_count: 2,
      patches: [],
      fixed_violations: [],
    },
    ...overrides,
  };
}

function panel(workflow: ProjectWorkflowController, withActions = true) {
  return (
    <MemoryRouter>
      <BUIProvider>
        <ProjectWorkflowPanel
          workflow={workflow}
          enableAi={false}
          feedbackEnabled={false}
          hostShipActions={
            withActions ? <button>Open in Dev Spaces</button> : undefined
          }
        />
      </BUIProvider>
    </MemoryRouter>
  );
}

describe("ProjectWorkflowPanel in the Portal host", () => {
  it.each([null, operation()])(
    "prioritizes cancellation and allows dismissal (%#)",
    (opState) => {
      const workflow = workflowStub({ isCancelling: true, opState });
      render(panel(workflow));

      expect(screen.getByText("Stopping session…")).toBeInTheDocument();
      expect(screen.queryByText("Starting scan…")).not.toBeInTheDocument();
      expect(screen.queryByText("Open in Dev Spaces")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(workflow.dismiss).toHaveBeenCalledTimes(1);
    },
  );

  it("shows the initial loading state and allows dismissal", () => {
    const workflow = workflowStub();
    render(panel(workflow));
    expect(screen.getByText("Starting scan…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(workflow.dismiss).toHaveBeenCalledTimes(1);
  });

  it("keeps host actions beside commit and PR actions through completion", async () => {
    const workflow = workflowStub({
      operationActive: true,
      opState: operation(),
      createPR: jest.fn().mockResolvedValue({
        branch_name: "apme/remediate-scan-1",
        commit_sha: "12345678",
        pr_url: "https://github.com/acme/repo/pull/12",
        provider: "github",
      }),
    });
    render(panel(workflow));

    const commit = screen.getByRole("button", { name: "Commit & open PR" });
    expect(
      screen.queryByRole("button", { name: "Open in Dev Spaces" }),
    ).not.toBeInTheDocument();
    fireEvent.click(commit);

    await waitFor(() =>
      expect(screen.getByText("Operation Complete")).toBeInTheDocument(),
    );
    expect(workflow.createPR).toHaveBeenCalledWith(
      expect.objectContaining({ create_pr: true }),
    );
    const pr = screen.getByRole("link", { name: /View pull request/i });
    expect(pr).toHaveAttribute(
      "href",
      "https://github.com/acme/repo/pull/12/files",
    );
    expect(
      screen.getAllByRole("button", { name: "Open in Dev Spaces" }),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Open in Dev Spaces" }).parentElement,
    ).toBe(pr.parentElement?.parentElement);
  });

  it("keeps terminal snapshots visible when operationActive is false", () => {
    render(
      panel(
        workflowStub({
          operationActive: false,
          opState: operation({
            status: "pr_submitted",
            pr_url: "https://github.com/acme/repo/pull/12",
          }),
        }),
      ),
    );
    expect(screen.getByText("Operation Complete")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open in Dev Spaces" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /View pull request/i }),
    ).toBeInTheDocument();
  });

  it("renders commit and completion without the optional slot", () => {
    const workflow = workflowStub({ opState: operation() });
    const { rerender } = render(panel(workflow, false));
    expect(
      screen.getByRole("button", { name: "Commit & open PR" }),
    ).toBeInTheDocument();
    rerender(
      panel(
        {
          ...workflow,
          opState: operation({
            status: "pr_submitted",
            pr_url: "https://github.com/acme/repo/pull/12",
          }),
        },
        false,
      ),
    );
    expect(screen.getByText("Operation Complete")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open in Dev Spaces" }),
    ).not.toBeInTheDocument();
  });

  it("shows and dismisses draft save errors", async () => {
    const log = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        panel(
          workflowStub({
            opState: operation({ status: "awaiting_approval", proposals: [] }),
            patchProposals: jest
              .fn()
              .mockRejectedValue(new Error("Draft unavailable")),
          }),
        ),
      );
      fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
      expect(await screen.findByText("Draft unavailable")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
      expect(screen.queryByText("Draft unavailable")).not.toBeInTheDocument();
    } finally {
      log.mockRestore();
    }
  });

  it("ignores draft failures arriving after the operation changes", async () => {
    let rejectDraft!: (error: Error) => void;
    const pending = new Promise<void>((_resolve, reject) => {
      rejectDraft = reject;
    });
    const workflow = workflowStub({
      opState: operation({ status: "awaiting_approval", proposals: [] }),
      patchProposals: jest.fn().mockReturnValue(pending),
    });
    const { rerender } = render(panel(workflow));
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    rerender(
      panel({ ...workflow, opState: operation({ operation_id: "op-2" }) }),
    );
    await act(async () => {
      rejectDraft(new Error("Old draft failure"));
    });
    expect(screen.queryByText("Old draft failure")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Could not save draft proposals"),
    ).not.toBeInTheDocument();
  });
});
