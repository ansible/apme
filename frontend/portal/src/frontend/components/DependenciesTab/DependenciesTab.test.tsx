import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { Violation } from "../../../common/types";
import {
  useApmeProjectContext,
  type ApmeProjectContext,
} from "../../hooks/useApmeProjectContext";
import { MOCK_PROJECTS } from "../../api/mock/fixtures";
import { DependenciesTab } from "./DependenciesTab";

jest.mock("../../hooks/useApmeProjectContext", () => ({
  useApmeProjectContext: jest.fn(),
}));
const mockAcknowledged = jest.fn(() => false);
let mockChanged: (() => void) | undefined;
const mockAcknowledge = jest.fn(() => {
  mockChanged?.();
  return Promise.resolve();
});
jest.mock("../../hooks/useViolationAcknowledge", () => ({
  useViolationAcknowledge: (_project: string, onChanged: () => void) => {
    mockChanged = onChanged;
    return {
      acknowledge: mockAcknowledge,
      unacknowledge: jest.fn(),
      acknowledgingId: null,
      isAcknowledged: mockAcknowledged,
    };
  },
}));
jest.mock("../ViolationDetailModal", () => ({
  ViolationDetailModal: ({
    title,
    onClose,
    onAcknowledge,
    violations,
  }: {
    title: string;
    onClose: () => void;
    onAcknowledge: (violation: Violation) => Promise<void>;
    violations: Violation[];
  }) => (
    <div role="dialog" aria-label={title}>
      <button onClick={onClose}>Close findings</button>
      <button
        onClick={() => {
          void onAcknowledge(violations[0]);
        }}
      >
        Acknowledge finding
      </button>
    </div>
  ),
}));

function Location() {
  return <output data-testid="location">{useLocation().search}</output>;
}

describe("DependenciesTab", () => {
  let state: ApmeProjectContext;
  const violation: Violation = {
    id: 1,
    rule_id: "R108",
    level: "high",
    message: "Dependency needs updating",
    file: "tasks/main.yml",
    line: 1,
    remediation_class: 3,
    validator_source: "collection_health",
    path: "community.general",
  };
  const pythonViolation: Violation = {
    ...violation,
    id: 2,
    rule_id: "CVE-1",
    validator_source: "dep_audit",
    message: "requests==1.0 is vulnerable",
    file: "requirements.txt",
  };
  beforeEach(() => {
    jest.clearAllMocks();
    mockAcknowledged.mockReturnValue(false);
    state = {
      repoUrl: null,
      branch: "main",
      project: { ...MOCK_PROJECTS[0] },
      violations: [],
      rulesById: new Map(),
      dependencies: null,
      loading: false,
      violationsLoading: false,
      dependenciesLoading: false,
      error: undefined,
      refresh: jest.fn(),
      refreshViolations: jest.fn(),
      registerAndScan: jest.fn(),
      registering: false,
      registerError: null,
    };
    (useApmeProjectContext as jest.Mock).mockImplementation(() => state);
  });
  const mount = () =>
    render(
      <MemoryRouter>
        <DependenciesTab
          context={{
            entity: {
              apiVersion: "backstage.io/v1alpha1",
              kind: "Component",
              metadata: { name: "repo" },
            },
            repoUrl: null,
          }}
        />
        <Location />
      </MemoryRouter>,
    );
  const dependencies = () => {
    state.dependencies = {
      ansible_core_version: "2.18",
      collections: [
        { fqcn: "community.general", version: "9.0", source: "specified" },
        { fqcn: "ansible.posix", version: "1.5", source: "learned" },
      ],
      python_packages: [
        { name: "requests", version: "1.0" },
        { name: "safe-package", version: "2.0" },
      ],
      requirements_files: [],
      dependency_tree: "",
    };
    state.violations = [violation, pythonViolation];
  };

  it.each(["loading", "dependenciesLoading", "violationsLoading"] as const)(
    "shows progress during %s",
    (key) => {
      state[key] = true;
      mount();
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    },
  );

  it("shows the unavailable state for connection failures", () => {
    state.error = new Error("Failed to connect to APME: fetch failed");
    mount();
    expect(screen.getByText("APME unavailable")).toBeInTheDocument();
  });

  it("offers a scan and routes it to the Quality dependency view", () => {
    state.project = null;
    mount();
    expect(screen.getByText("No quality scans yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    expect(screen.getByTestId("location")).toHaveTextContent("tab=quality");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "category=dependencies",
    );
  });

  it("distinguishes missing dependency data from a successful empty result", () => {
    const { unmount } = mount();
    expect(
      screen.getByText("Run a quality scan to discover dependencies."),
    ).toBeInTheDocument();
    unmount();
    dependencies();
    state.dependencies!.collections = [];
    state.dependencies!.python_packages = [];
    mount();
    expect(screen.getByText("No collections detected")).toBeInTheDocument();
    expect(screen.getByText("No Python packages detected")).toBeInTheDocument();
  });

  it("renders both dependency tables, source labels, and violation summaries", () => {
    dependencies();
    mount();
    expect(screen.getByText("Collections (2)")).toBeInTheDocument();
    expect(screen.getByText("Python packages (2)")).toBeInTheDocument();
    expect(screen.getByText("community.general")).toBeInTheDocument();
    expect(screen.getByText("requests")).toBeInTheDocument();
    expect(screen.getByText("learned")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "quality scan" }), {
      key: "Enter",
    });
    expect(screen.getByTestId("location")).toHaveTextContent("tab=quality");
  });

  it("opens collection findings using the keyboard and refreshes only after the dialog closes", () => {
    dependencies();
    mount();
    const row = screen
      .getByText("community.general")
      .closest("tr") as HTMLElement;
    fireEvent.keyDown(within(row).getByRole("button"), { key: " " });
    const dialog = screen.getByRole("dialog", {
      name: "community.general 9.0",
    });
    fireEvent.click(within(dialog).getByText("Acknowledge finding"));
    expect(mockAcknowledge).toHaveBeenCalledWith(violation);
    expect(state.refreshViolations).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByText("Close findings"));
    expect(state.refreshViolations).toHaveBeenCalledTimes(1);
  });

  it("opens Python findings and immediately refreshes changes made outside a dialog", () => {
    dependencies();
    mount();
    act(() => {
      mockChanged?.();
    });
    expect(state.refreshViolations).toHaveBeenCalledTimes(1);
    const row = screen.getByText("requests").closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button"));
    expect(
      screen.getByRole("dialog", { name: "requests" }),
    ).toBeInTheDocument();
  });

  it("does not count acknowledged findings as active violations", () => {
    dependencies();
    mockAcknowledged.mockReturnValue(true);
    mount();
    const row = screen
      .getByText("community.general")
      .closest("tr") as HTMLElement;
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });
});
