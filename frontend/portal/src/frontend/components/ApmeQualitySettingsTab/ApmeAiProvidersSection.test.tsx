/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import { apmeApiRef } from "../../api";
import { ApmeAiProvidersSection } from "./ApmeAiProvidersSection";

describe("ApmeAiProvidersSection", () => {
  const getAiProviders = jest.fn();
  const getAiStatus = jest.fn();
  const getAiModels = jest.fn();
  const getAiConfig = jest.fn();
  const getAiEngines = jest.fn();
  const configureAiProvider = jest.fn();
  const updateAiConfig = jest.fn();
  const deleteAiProvider = jest.fn();
  const updatePortalSettings = jest.fn();

  const apmeApi = {
    getAiProviders,
    getAiStatus,
    getAiModels,
    getAiConfig,
    getAiEngines,
    configureAiProvider,
    updateAiConfig,
    deleteAiProvider,
    updatePortalSettings,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    getAiStatus.mockResolvedValue({
      enableAi: true,
      connected: true,
      modelCount: 2,
    });
    getAiProviders.mockResolvedValue([]);
    getAiModels.mockResolvedValue([]);
    getAiConfig.mockResolvedValue(undefined);
    getAiEngines.mockResolvedValue({
      engines: [
        { id: "openai", requiresKey: true, defaultEnvVar: "OPENAI_API_KEY" },
        {
          id: "anthropic",
          requiresKey: true,
          defaultEnvVar: "ANTHROPIC_API_KEY",
        },
      ],
    });
    configureAiProvider.mockResolvedValue(undefined);
    updateAiConfig.mockResolvedValue(undefined);
    updatePortalSettings.mockResolvedValue({});
  });

  function renderSection(props?: { fillHeight?: boolean }) {
    return render(
      <TestApiProvider apis={[[apmeApiRef, apmeApi as any]]}>
        <ApmeAiProvidersSection fillHeight={props?.fillHeight} />
      </TestApiProvider>,
    );
  }

  it("renders AI providers card heading", async () => {
    renderSection();
    expect(await screen.findByText("AI providers")).toBeInTheDocument();
  });

  it("shows connected status chip from getAiStatus", async () => {
    renderSection();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
  });

  it("shows Connected without model-count details when only configured models exist", async () => {
    getAiStatus.mockResolvedValue({
      enableAi: true,
      connected: true,
      modelCount: 0,
      configuredModelCount: 2,
    });
    renderSection();
    const status = await screen.findByText("Connected");
    expect(status).toBeInTheDocument();
    expect(status.textContent).toBe("Connected");
    expect(
      screen.queryByText(/configured \(0 inference\)/i),
    ).not.toBeInTheDocument();
  });

  it("shows disconnected status chip when Abbenay is unreachable", async () => {
    getAiStatus.mockResolvedValue({
      enableAi: true,
      connected: false,
      modelCount: 0,
    });
    renderSection();
    expect(await screen.findByText("Disconnected")).toBeInTheDocument();
  });

  it("shows empty state when no providers", async () => {
    renderSection();
    expect(await screen.findByText("No providers added")).toBeInTheDocument();
    expect(
      screen.getByText(
        /configure an AI provider to enable AI-assisted remediation/i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/deploy-time config/i)).not.toBeInTheDocument();
  });

  it("shows supported engines at the bottom when fillHeight and no providers", async () => {
    renderSection({ fillHeight: true });
    expect(await screen.findByText("Supported engines")).toBeInTheDocument();
    expect(screen.getByTitle("openai (API key required)")).toBeInTheDocument();
    expect(
      screen.getByTitle("anthropic (API key required)"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Getting started")).not.toBeInTheDocument();
  });

  it("does not show supported engines footer when models are available", async () => {
    getAiModels.mockResolvedValue([
      { id: "gpt-4o", provider: "openai", name: "GPT-4o" },
    ]);
    renderSection({ fillHeight: true });
    expect(await screen.findByText("Available models")).toBeInTheDocument();
    expect(screen.queryByText("Supported engines")).not.toBeInTheDocument();
  });

  it("shows ConfigMap providers as read-only system section", async () => {
    getAiProviders.mockResolvedValue([]);
    getAiConfig.mockResolvedValue({
      config: {
        providers: {
          "cm-prov": { engine: "openai", models: { "gpt-4o": {} } },
        },
      },
    });
    renderSection();

    expect(await screen.findByText("System providers")).toBeInTheDocument();
    expect(screen.getByText("No providers added")).toBeInTheDocument();
    expect(
      screen.getByText(/system providers from your deployment ConfigMap/i),
    ).toBeInTheDocument();
    expect(screen.getByText("cm-prov")).toBeInTheDocument();
    expect(screen.getByText("Source: ConfigMap")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /edit provider cm-prov/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove provider cm-prov/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps managed providers editable when ConfigMap also lists them", async () => {
    getAiProviders.mockResolvedValue([
      { id: "shared", engine: "openai", models: ["gpt-4o"] },
    ]);
    getAiConfig.mockResolvedValue({
      config: {
        providers: {
          shared: { engine: "openai", models: { "gpt-4o": {} } },
          "cm-only": { engine: "anthropic", models: {} },
        },
      },
    });
    renderSection();

    expect(await screen.findByText("shared")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /edit provider shared/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("System providers")).toBeInTheDocument();
    expect(screen.getByText("cm-only")).toBeInTheDocument();
  });

  it("shows available models section and model chips when providers empty but models returned", async () => {
    getAiModels.mockResolvedValue([
      { id: "gpt-4o", provider: "openai", name: "GPT-4o" },
      { id: "claude-3-opus", provider: "anthropic", name: "Claude 3 Opus" },
    ]);
    renderSection();
    expect(await screen.findByText("No providers added")).toBeInTheDocument();
    expect(
      screen.getByText(/models listed below are read-only from Primary/i),
    ).toBeInTheDocument();
    expect(await screen.findByText("Available models")).toBeInTheDocument();
    expect(screen.getByTitle("gpt-4o (openai)")).toBeInTheDocument();
    expect(screen.getByTitle("claude-3-opus (anthropic)")).toBeInTheDocument();
    expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    expect(screen.getByText("Claude 3 Opus")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /view all \d+ available models/i }),
    ).not.toBeInTheDocument();
  });

  it("shows only three model chips with view-all button when more than three models exist", async () => {
    getAiModels.mockResolvedValue([
      { id: "model-1", provider: "openai", name: "Model 1" },
      { id: "model-2", provider: "openai", name: "Model 2" },
      { id: "model-3", provider: "openai", name: "Model 3" },
      { id: "model-4", provider: "anthropic", name: "Model 4" },
      { id: "model-5", provider: "anthropic", name: "Model 5" },
    ]);
    renderSection();

    expect(await screen.findByText("Available models")).toBeInTheDocument();
    expect(screen.getByText("Model 1")).toBeInTheDocument();
    expect(screen.getByText("Model 2")).toBeInTheDocument();
    expect(screen.getByText("Model 3")).toBeInTheDocument();
    expect(screen.queryByText("Model 4")).not.toBeInTheDocument();
    expect(screen.queryByText("Model 5")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /view all 5 available models/i }),
    ).toHaveTextContent("View all models (2 more)");
  });

  it("opens modal with all models when view-all button is clicked", async () => {
    getAiModels.mockResolvedValue([
      { id: "model-1", provider: "openai", name: "Model 1" },
      { id: "model-2", provider: "openai", name: "Model 2" },
      { id: "model-3", provider: "openai", name: "Model 3" },
      { id: "model-4", provider: "anthropic", name: "Model 4" },
    ]);
    renderSection();

    fireEvent.click(
      await screen.findByRole("button", {
        name: /view all 4 available models/i,
      }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(
      screen.getByRole("heading", { name: "Available models" }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Model 1");
    expect(dialog).toHaveTextContent("Model 2");
    expect(dialog).toHaveTextContent("Model 3");
    expect(dialog).toHaveTextContent("Model 4");

    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("renders provider list with engine chip and model count", async () => {
    getAiProviders.mockResolvedValue([
      {
        id: "my-openrouter",
        engine: "openrouter",
        models: ["gpt-4o", "gpt-4"],
      },
    ]);
    renderSection();
    expect(await screen.findByText("my-openrouter")).toBeInTheDocument();
    expect(screen.getByText("openrouter")).toBeInTheDocument();
    expect(screen.getByText(/2 models: gpt-4o, gpt-4/)).toBeInTheDocument();
  });

  it("renders providers sorted by id when API returns unsorted list", async () => {
    getAiProviders.mockResolvedValue([
      { id: "zeta-provider", engine: "openai", models: [] },
      { id: "Alpha-provider", engine: "anthropic", models: [] },
      { id: "beta-provider", engine: "openrouter", models: [] },
    ]);
    renderSection();
    await screen.findByText("Alpha-provider");

    const items = screen.getAllByRole("listitem");
    const ids = items.map((item) => item.textContent ?? "");
    expect(ids[0]).toMatch(/Alpha-provider/);
    expect(ids[1]).toMatch(/beta-provider/);
    expect(ids).toHaveLength(2);
  });

  it("renders a single provider without a view-all button", async () => {
    getAiProviders.mockResolvedValue([
      { id: "only-provider", engine: "ollama", models: ["llama3.2:1b"] },
    ]);
    renderSection();

    expect(await screen.findByText("only-provider")).toBeInTheDocument();
    expect(screen.getByText("1 model: llama3.2:1b")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /view all \d+ ai providers/i }),
    ).not.toBeInTheDocument();
  });

  it("stretches card when fillHeight with a single provider", async () => {
    getAiProviders.mockResolvedValue([
      { id: "only-provider", engine: "ollama", models: ["llama3.2:1b"] },
    ]);
    getAiModels.mockResolvedValue([
      { id: "llama3.2:1b", provider: "ollama", name: "llama3.2:1b" },
    ]);
    renderSection({ fillHeight: true });

    expect(await screen.findByText("only-provider")).toBeInTheDocument();
    expect(screen.getByText("Available models")).toBeInTheDocument();
    expect(
      screen.queryByTestId("abbenay-connection-summary"),
    ).not.toBeInTheDocument();
  });

  it("truncates long provider model lists in the card", async () => {
    getAiProviders.mockResolvedValue([
      {
        id: "local-ollama",
        engine: "ollama",
        models: ["m1", "m2", "m3", "m4", "m5"],
      },
    ]);
    renderSection();
    expect(
      await screen.findByText("5 models: m1, m2, m3 +2 more"),
    ).toBeInTheDocument();
  });

  it("shows no models configured when provider has an empty model list", async () => {
    getAiProviders.mockResolvedValue([
      { id: "empty-provider", engine: "openai", models: [] },
    ]);
    renderSection();
    expect(await screen.findByText("No models configured")).toBeInTheDocument();
  });

  it("shows all model names when provider has exactly three models", async () => {
    getAiProviders.mockResolvedValue([
      {
        id: "triple-provider",
        engine: "ollama",
        models: ["model-a", "model-b", "model-c"],
      },
    ]);
    renderSection();
    expect(
      await screen.findByText("3 models: model-a, model-b, model-c"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\+/)).not.toBeInTheDocument();
  });

  it("exposes full model list via title tooltip when summary is truncated", async () => {
    getAiProviders.mockResolvedValue([
      {
        id: "local-ollama",
        engine: "ollama",
        models: ["m1", "m2", "m3", "m4", "m5"],
      },
    ]);
    renderSection();
    const summary = await screen.findByText("5 models: m1, m2, m3 +2 more");
    expect(summary).toHaveAttribute("title", "m1, m2, m3, m4, m5");
  });

  it("truncates ConfigMap system provider model summaries", async () => {
    getAiProviders.mockResolvedValue([]);
    getAiConfig.mockResolvedValue({
      config: {
        providers: {
          "cm-ollama": {
            engine: "ollama",
            models: {
              m1: {},
              m2: {},
              m3: {},
              m4: {},
            },
          },
        },
      },
    });
    renderSection();

    expect(await screen.findByText("System providers")).toBeInTheDocument();
    expect(
      screen.getByText("4 models: m1, m2, m3 +1 more"),
    ).toBeInTheDocument();
  });

  it("applies fillHeight layout classes when fillHeight is true", async () => {
    getAiProviders.mockResolvedValue([
      { id: "only-provider", engine: "ollama", models: ["llama3.2:1b"] },
    ]);
    getAiModels.mockResolvedValue([
      { id: "llama3.2:1b", provider: "ollama", name: "llama3.2:1b" },
    ]);
    const { container } = renderSection({ fillHeight: true });
    await screen.findByText("only-provider");

    const card = container.querySelector(".MuiCard-root");
    const cardContent = container.querySelector(".MuiCardContent-root");
    expect(card?.className).toEqual(expect.stringMatching(/fillHeightCard/));
    expect(cardContent?.className).toEqual(
      expect.stringMatching(/fillHeightContent/),
    );

    const modelsHeading = screen.getByText("Available models");
    const modelsSection = modelsHeading.parentElement;
    expect(modelsSection?.className).toEqual(
      expect.stringMatching(/modelsSectionAtBottom/),
    );
  });

  it("does not apply fillHeight layout classes when fillHeight is false", async () => {
    getAiProviders.mockResolvedValue([
      { id: "only-provider", engine: "ollama", models: ["llama3.2:1b"] },
    ]);
    const { container } = renderSection();
    await screen.findByText("only-provider");

    const card = container.querySelector(".MuiCard-root");
    const cardContent = container.querySelector(".MuiCardContent-root");
    expect(card?.className).not.toEqual(
      expect.stringMatching(/fillHeightCard/),
    );
    expect(cardContent?.className).not.toEqual(
      expect.stringMatching(/fillHeightContent/),
    );
  });

  it("shows only two providers with view-all button when more than two exist", async () => {
    getAiProviders.mockResolvedValue([
      { id: "provider-1", engine: "ollama", models: ["model-a"] },
      { id: "provider-2", engine: "ollama", models: ["model-b"] },
      { id: "provider-3", engine: "ollama", models: ["model-c"] },
      { id: "provider-4", engine: "ollama", models: ["model-d"] },
      { id: "provider-5", engine: "ollama", models: ["model-e"] },
    ]);
    renderSection();

    expect(await screen.findByText("provider-1")).toBeInTheDocument();
    expect(screen.getByText("provider-2")).toBeInTheDocument();
    expect(screen.queryByText("provider-3")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /view all 5 ai providers/i }),
    ).toHaveTextContent("View all providers (3 more)");
  });

  it("opens modal with all providers when view-all button is clicked", async () => {
    getAiProviders.mockResolvedValue([
      { id: "provider-1", engine: "ollama", models: ["model-a"] },
      { id: "provider-2", engine: "ollama", models: ["model-b"] },
      { id: "provider-3", engine: "ollama", models: ["model-c"] },
    ]);
    renderSection();

    fireEvent.click(
      await screen.findByRole("button", { name: /view all 3 ai providers/i }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(
      screen.getByRole("heading", { name: "AI providers" }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("provider-1");
    expect(dialog).toHaveTextContent("provider-2");
    expect(dialog).toHaveTextContent("provider-3");

    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("opens edit dialog from view-all providers modal", async () => {
    getAiProviders.mockResolvedValue([
      { id: "provider-1", engine: "ollama", models: ["model-a"] },
      { id: "provider-2", engine: "ollama", models: ["model-b"] },
      { id: "provider-3", engine: "ollama", models: ["model-c"] },
    ]);
    renderSection();

    fireEvent.click(
      await screen.findByRole("button", { name: /view all 3 ai providers/i }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /edit provider provider-3/i }),
    );

    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "AI providers" }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText("Edit provider: provider-3")).toBeInTheDocument();
  });

  it("opens remove confirm from view-all providers modal", async () => {
    getAiProviders.mockResolvedValue([
      { id: "provider-1", engine: "ollama", models: ["model-a"] },
      { id: "provider-2", engine: "ollama", models: ["model-b"] },
      { id: "provider-3", engine: "ollama", models: ["model-c"] },
    ]);
    renderSection();

    fireEvent.click(
      await screen.findByRole("button", { name: /view all 3 ai providers/i }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /remove provider provider-2/i }),
    );

    expect(
      await screen.findByRole("heading", { name: /remove provider/i }),
    ).toBeInTheDocument();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Remove provider provider-2?");
  });

  it('opens add dialog on "Add provider" click', async () => {
    renderSection();
    await screen.findByText("AI providers");
    fireEvent.click(screen.getByRole("button", { name: /add provider/i }));
    expect(screen.getByText("Add AI provider")).toBeInTheDocument();
  });

  it("opens edit dialog on Edit icon click", async () => {
    getAiProviders.mockResolvedValue([
      { id: "my-provider", engine: "anthropic", models: ["claude-3"] },
    ]);
    renderSection();
    const editBtn = await screen.findByRole("button", {
      name: /edit provider my-provider/i,
    });
    fireEvent.click(editBtn);
    expect(screen.getByText("Edit provider: my-provider")).toBeInTheDocument();
  });

  it("shows remove confirm dialog and calls deleteAiProvider on confirm", async () => {
    getAiProviders.mockResolvedValueOnce([
      { id: "my-provider", engine: "anthropic", models: [] },
    ]);
    deleteAiProvider.mockResolvedValue(undefined);
    // After delete, reload returns empty list
    getAiProviders.mockResolvedValueOnce([]);

    renderSection();
    const removeBtn = await screen.findByRole("button", {
      name: /remove provider my-provider/i,
    });
    fireEvent.click(removeBtn);

    // Confirm dialog should appear
    expect(
      await screen.findByRole("heading", { name: /remove provider/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(deleteAiProvider).toHaveBeenCalledWith("my-provider");
    });
  });

  it("shows error when deleteAiProvider fails", async () => {
    getAiProviders.mockResolvedValueOnce([
      { id: "fail-provider", engine: "openrouter", models: [] },
    ]);
    deleteAiProvider.mockRejectedValue(new Error("Gateway timeout"));

    renderSection();
    const removeBtn = await screen.findByRole("button", {
      name: /remove provider fail-provider/i,
    });
    fireEvent.click(removeBtn);

    fireEvent.click(await screen.findByRole("button", { name: /^remove$/i }));

    expect(await screen.findByText("Gateway timeout")).toBeInTheDocument();
  });

  it("save calls configureAiProvider with apiKey + secretStore file then updateAiConfig with merged models", async () => {
    getAiConfig.mockResolvedValue({
      config: {
        providers: { "existing-prov": { engine: "anthropic" } },
        server: {},
      },
    });

    renderSection();
    await screen.findByText("AI providers");

    fireEvent.click(screen.getByRole("button", { name: /add provider/i }));
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "new-prov" },
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
      expect(configureAiProvider).toHaveBeenCalledWith("new-prov", {
        engine: "openai",
        apiKey: "sk-test",
        secretStore: "file",
        secretName: "OPENAI_API_KEY",
      });
    });
    await waitFor(() => {
      expect(updateAiConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          location: "user",
          config: expect.objectContaining({
            providers: expect.objectContaining({
              "new-prov": expect.objectContaining({
                engine: "openai",
                models: { "gpt-4o": {} },
              }),
              "existing-prov": expect.objectContaining({ engine: "anthropic" }),
            }),
          }),
        }),
      );
    });
    await waitFor(() => {
      expect(updatePortalSettings).toHaveBeenCalledWith({
        defaultAiModelId: "new-prov/gpt-4o",
      });
    });
  });

  it("save starts from an empty providers map when AI config has none", async () => {
    getAiConfig.mockResolvedValue(undefined);

    renderSection();
    await screen.findByText("AI providers");

    fireEvent.click(screen.getByRole("button", { name: /add provider/i }));
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "fresh-prov" },
    });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));
    await screen.findByLabelText("Model ID");
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(updateAiConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          config: expect.objectContaining({
            providers: expect.objectContaining({
              "fresh-prov": expect.objectContaining({ engine: "openai" }),
            }),
          }),
        }),
      );
    });
  });

  it("save merges new provider models while preserving existing providers and server config", async () => {
    getAiConfig.mockResolvedValue({
      config: {
        providers: {
          "other-prov": { engine: "anthropic", models: { "claude-3": {} } },
        },
        server: { port: 8080 },
      },
    });

    renderSection();
    await screen.findByText("AI providers");

    fireEvent.click(screen.getByRole("button", { name: /add provider/i }));
    fireEvent.change(screen.getByLabelText("Provider name"), {
      target: { value: "my-prov" },
    });

    await waitFor(() =>
      expect(screen.getByLabelText(/Engine/i)).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next: models/i }));
    await screen.findByLabelText("Model ID");

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(updateAiConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          config: expect.objectContaining({
            server: { port: 8080 },
            providers: expect.objectContaining({
              "other-prov": expect.objectContaining({ engine: "anthropic" }),
              "my-prov": expect.objectContaining({ engine: "openai" }),
            }),
          }),
        }),
      );
    });
  });
});
