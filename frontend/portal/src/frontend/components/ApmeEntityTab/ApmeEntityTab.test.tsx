/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import { configApiRef } from "@backstage/core-plugin-api";
import { ConfigReader } from "@backstage/config";
import { apmeApiRef } from "../../api";
import { ApmeEntityTab } from "./ApmeEntityTab";

const mockUseApmeAiEnabled = jest.fn(() => false);
const mockUseProjectWorkflow = jest.fn();

jest.mock("../../hooks/useApmeEnabled", () => ({
  useApmeAiEnabled: () => mockUseApmeAiEnabled(),
}));

jest.mock("../../hooks/useApmeWorkflowAiModel", () => ({
  useApmeWorkflowAiModel: () => jest.fn(async () => "test-model"),
}));

jest.mock("../../hooks/useSyncPatternFlyTheme", () => ({
  useSyncPatternFlyTheme: jest.fn(),
}));

jest.mock("../../hooks/useResolveApmeProject", () => ({
  useResolveApmeProject: () => ({
    adapter: {},
    projectId: "proj-1",
    error: null,
    unavailable: false,
  }),
}));

const mockResolveVersion = jest.fn<Promise<string>, []>(async () => "2.16");
jest.mock("../../utils/resolveDefaultAnsibleVersionForScan", () => ({
  resolveDefaultAnsibleVersionForScan: () => mockResolveVersion(),
}));

jest.mock("../../utils/resolvePostPushDevSpacesUrl", () => ({
  resolvePostPushDevSpacesUrl: jest.fn(() => undefined),
}));

jest.mock("../ApmeUnavailable", () => ({
  ApmeUnavailable: () => <div>unavailable</div>,
}));

jest.mock("../EditInDevSpacesButton", () => ({
  EditInDevSpacesButton: () => null,
}));

jest.mock("../PortalWorkflowPanel", () => ({
  PortalProjectWorkflowPanel: () => <div data-testid="workflow-panel" />,
}));

jest.mock("../PreviewChip", () => ({
  PreviewLabelRow: () => <div data-testid="preview-label" />,
}));

jest.mock("@apme/ui-workflow", () => ({
  ApmeApiProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  CheckOptionsForm: (props: {
    showCollections?: boolean;
    showAiOptions?: boolean;
    enableAi: boolean;
    autoApplyTier1?: boolean;
    onAutoApplyTier1Change?: (checked: boolean) => void;
    onEnableAiChange: (checked: boolean) => void;
  }) => (
    <div data-testid="check-options">
      <span data-testid="show-collections">
        {String(props.showCollections ?? true)}
      </span>
      <span data-testid="show-ai-options">{String(!!props.showAiOptions)}</span>
      <span data-testid="form-enable-ai">{String(props.enableAi)}</span>
      <span data-testid="form-auto-apply">
        {String(!!props.autoApplyTier1)}
      </span>
      <button
        type="button"
        onClick={() => props.onAutoApplyTier1Change?.(true)}
      >
        enable-auto-apply
      </button>
      <button type="button" onClick={() => props.onEnableAiChange(true)}>
        enable-ai-checkbox
      </button>
    </div>
  ),
  useProjectWorkflow: (...args: unknown[]) => mockUseProjectWorkflow(...args),
}));

describe("ApmeEntityTab", () => {
  const getProject = jest.fn();

  const workflowStub = {
    sessionTabVisible: false,
    isRunning: false,
    startScan: jest.fn(),
    cancel: jest.fn(),
    dismiss: jest.fn(),
    createPR: jest.fn(),
    opState: null,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    mockUseApmeAiEnabled.mockReturnValue(false);
    mockUseProjectWorkflow.mockReturnValue(workflowStub);
    getProject.mockResolvedValue({
      id: "proj-1",
      name: "demo-repo",
      repo_url: "https://github.com/example/demo",
      branch: "main",
      health_score: 80,
      total_violations: 2,
      scan_count: 3,
    });
  });

  function renderTab() {
    return render(
      <TestApiProvider
        apis={[
          [apmeApiRef, { getProject }],
          [
            configApiRef,
            new ConfigReader({
              ansible: { apme: { enableAi: false }, devSpaces: {} },
            }),
          ],
        ]}
      >
        <ApmeEntityTab />
      </TestApiProvider>,
    );
  }

  function latestCheckOptions() {
    const calls = mockUseProjectWorkflow.mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    return calls[calls.length - 1][1].checkOptions as {
      enableAi: boolean;
      autoApplyTier1: boolean;
    };
  }

  it("keeps default ansible version when resolveDefaultAnsibleVersionForScan rejects", async () => {
    mockResolveVersion.mockRejectedValueOnce(new Error("network error"));
    renderTab();
    // The catch block swallows the error — component renders without crashing
    expect(await screen.findByText("demo-repo")).toBeInTheDocument();
  });

  it("hides the collections input from the portal form (AAP-89526)", async () => {
    renderTab();
    expect(await screen.findByText("demo-repo")).toBeInTheDocument();
    expect(screen.getByTestId("show-collections")).toHaveTextContent("false");
  });

  it("hides AI options and forces checkOptions AI flags false when portal AI is disabled", async () => {
    mockUseApmeAiEnabled.mockReturnValue(false);
    renderTab();

    expect(await screen.findByText("demo-repo")).toBeInTheDocument();
    expect(screen.getByTestId("show-ai-options")).toHaveTextContent("false");
    expect(screen.getByTestId("form-enable-ai")).toHaveTextContent("false");
    expect(
      screen.getByText("AI is disabled in Quality settings."),
    ).toBeInTheDocument();

    await waitFor(() => {
      const opts = latestCheckOptions();
      expect(opts.enableAi).toBe(false);
      expect(opts.autoApplyTier1).toBe(false);
    });
  });

  it("does not show AI disabled note when portal AI is enabled", async () => {
    mockUseApmeAiEnabled.mockReturnValue(true);
    renderTab();
    await screen.findByText("demo-repo");

    expect(
      screen.queryByText("AI is disabled in Quality settings."),
    ).not.toBeInTheDocument();
  });

  it("keeps checkOptions.autoApplyTier1 false when portal AI is disabled even after stale auto-apply toggle", async () => {
    mockUseApmeAiEnabled.mockReturnValue(false);
    renderTab();
    await screen.findByText("demo-repo");

    fireEvent.click(screen.getByRole("button", { name: "enable-auto-apply" }));

    await waitFor(() => {
      expect(latestCheckOptions().autoApplyTier1).toBe(false);
    });
    // Form may hold local autoApply state, but gateway options stay gated.
    expect(latestCheckOptions().enableAi).toBe(false);
  });

  it("passes autoApplyTier1 through to the workflow when portal AI is enabled", async () => {
    mockUseApmeAiEnabled.mockReturnValue(true);
    renderTab();
    await screen.findByText("demo-repo");

    expect(screen.getByTestId("show-ai-options")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "enable-auto-apply" }));

    await waitFor(() => {
      expect(latestCheckOptions().autoApplyTier1).toBe(true);
      expect(latestCheckOptions().enableAi).toBe(true);
    });
  });

  it("resets autoApplyTier1 for the gateway when portal AI flips from enabled to disabled", async () => {
    mockUseApmeAiEnabled.mockReturnValue(true);
    const { rerender } = renderTab();
    await screen.findByText("demo-repo");

    fireEvent.click(screen.getByRole("button", { name: "enable-auto-apply" }));
    await waitFor(() => {
      expect(latestCheckOptions().autoApplyTier1).toBe(true);
    });

    mockUseApmeAiEnabled.mockReturnValue(false);
    rerender(
      <TestApiProvider
        apis={[
          [apmeApiRef, { getProject }],
          [
            configApiRef,
            new ConfigReader({
              ansible: { apme: { enableAi: false }, devSpaces: {} },
            }),
          ],
        ]}
      >
        <ApmeEntityTab />
      </TestApiProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("show-ai-options")).toHaveTextContent("false");
      expect(latestCheckOptions().autoApplyTier1).toBe(false);
      expect(latestCheckOptions().enableAi).toBe(false);
    });
  });
});
