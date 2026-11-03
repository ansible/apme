/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApmeAiProviderDialog } from "./ApmeAiProviderDialog";

// Mock useApi so the dialog can call getAiEngines without a real Backstage context.
const mockGetAiEngines = jest.fn();
jest.mock("@backstage/core-plugin-api", () => ({
  ...jest.requireActual("@backstage/core-plugin-api"),
  useApi: () => ({ getAiEngines: mockGetAiEngines }),
}));

const MOCK_ENGINES = [
  { id: "openai", requiresKey: true, defaultEnvVar: "OPENAI_API_KEY" },
  {
    id: "ollama",
    requiresKey: false,
    defaultBaseUrl: "http://localhost:11434",
  },
  { id: "redhat", requiresKey: true },
];

describe("ApmeAiProviderDialog", () => {
  const onClose = jest.fn();
  const onSave = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    onSave.mockResolvedValue(undefined);
    mockGetAiEngines.mockResolvedValue({ engines: MOCK_ENGINES });
  });

  function renderDialog(props?: { provider?: any }) {
    return render(
      <ApmeAiProviderDialog
        open
        provider={props?.provider}
        onClose={onClose}
        onSave={onSave}
      />,
    );
  }

  it("renders step 1 setup form for add", async () => {
    renderDialog();
    expect(screen.getByText("Add AI provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider name")).toBeInTheDocument();
    // Engine select appears after engines load
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
  });

  it("lists live engines from getAiEngines (openai, ollama, redhat)", async () => {
    renderDialog();
    // Wait for engines to load
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByLabelText(/Engine/i));
    expect(screen.getByRole("option", { name: "openai" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ollama" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "redhat" })).toBeInTheDocument();
  });

  it("filters out mock engine from live list", async () => {
    mockGetAiEngines.mockResolvedValue({
      engines: [...MOCK_ENGINES, { id: "mock", requiresKey: false }],
    });
    renderDialog();
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByLabelText(/Engine/i));
    expect(
      screen.queryByRole("option", { name: "mock" }),
    ).not.toBeInTheDocument();
  });

  it("renders edit title when provider is supplied", async () => {
    renderDialog({
      provider: { id: "my-prov", engine: "openai", models: ["gpt-4o"] },
    });
    expect(screen.getByText("Edit provider: my-prov")).toBeInTheDocument();
    // ID field should not appear in edit mode
    expect(screen.queryByLabelText("Provider name")).not.toBeInTheDocument();
  });

  it("shows unknown engine in edit mode even if not in live list", async () => {
    renderDialog({
      provider: { id: "my-prov", engine: "custom-engine", models: [] },
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByLabelText(/Engine/i));
    expect(
      screen.getByRole("option", { name: "custom-engine" }),
    ).toBeInTheDocument();
  });

  it("shows loading spinner while engines load", () => {
    // Never resolves during this test
    mockGetAiEngines.mockReturnValue(new Promise(() => {}));
    renderDialog();
    expect(screen.getByText(/Loading engines/i)).toBeInTheDocument();
  });

  it("shows error when engine load fails", async () => {
    mockGetAiEngines.mockRejectedValue(new Error("network error"));
    renderDialog();
    expect(await screen.findByText("network error")).toBeInTheDocument();
    // Engine select should not render on error
    expect(screen.queryByLabelText(/Engine/i)).not.toBeInTheDocument();
  });

  it('advances to step 2 on "Next: Models"', async () => {
    renderDialog();
    const idField = screen.getByLabelText("Provider name");
    fireEvent.change(idField, { target: { value: "my-provider" } });

    // Wait for engine select to appear (engines loaded)
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));

    expect(await screen.findByText(/Step 2 of 2/)).toBeInTheDocument();
    expect(screen.getByLabelText("Model ID")).toBeInTheDocument();
  });

  it("adds model chips on step 2 and shows chip list", async () => {
    renderDialog();
    const idField = screen.getByLabelText("Provider name");
    fireEvent.change(idField, { target: { value: "my-provider" } });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));

    const modelInput = await screen.findByLabelText("Model ID");
    fireEvent.change(modelInput, { target: { value: "gpt-4o" } });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: /enabled models/i }),
    ).toBeInTheDocument();

    // Adding the same model again does not duplicate it
    fireEvent.change(screen.getByLabelText("Model ID"), {
      target: { value: "gpt-4o" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(screen.getAllByText("gpt-4o")).toHaveLength(1);
  });

  it("requires API key before advancing when engine needs a key", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "my-provider" },
    });
    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /next: models/i }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    expect(
      screen.getByRole("button", { name: /next: models/i }),
    ).not.toBeDisabled();
  });

  it("calls onSave with apiKey + secretStore file and models separately", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "test-prov" },
    });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));

    const modelInput = await screen.findByLabelText("Model ID");
    fireEvent.change(modelInput, { target: { value: "gpt-4o" } });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("test-prov", {
        configure: {
          engine: "openai", // first engine in MOCK_ENGINES
          apiKey: "sk-test",
          secretStore: "file",
          secretName: "OPENAI_API_KEY",
        },
        models: ["gpt-4o"],
      });
    });
  });

  it("omits apiKey from configure when left blank (edit)", async () => {
    renderDialog({
      provider: { id: "existing", engine: "openai", models: ["gpt-4o"] },
    });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));
    await screen.findByText(/Step 2 of 2/);

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        "existing",
        expect.objectContaining({
          configure: expect.objectContaining({
            engine: "openai",
            secretStore: "file",
          }),
        }),
      );
      expect(onSave.mock.calls[0][1].configure.apiKey).toBeUndefined();
    });
  });

  it("shows error when onSave rejects", async () => {
    onSave.mockRejectedValue(new Error("configure failed"));
    renderDialog();
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "p1" },
    });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));
    await screen.findByText(/Step 2 of 2/);

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("configure failed")).toBeInTheDocument();
  });

  it("calls onClose on Cancel", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
