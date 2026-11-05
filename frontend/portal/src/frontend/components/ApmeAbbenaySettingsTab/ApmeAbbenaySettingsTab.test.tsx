/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderInTestApp, TestApiProvider } from "@backstage/test-utils";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { apmeApiRef } from "../../api";
import { ApmeAbbenaySettingsTab } from "./ApmeAbbenaySettingsTab";

jest.mock("@backstage/plugin-permission-react", () => ({
  RequirePermission: (props: any) => props.children,
}));

const theme = createTheme();

const mockApmeApi = {
  getPortalSettings: jest.fn(),
  updatePortalSettings: jest.fn(),
  getAiStatus: jest.fn(),
  getAiModels: jest.fn(),
  getAiProviders: jest.fn(),
  getAiEngines: jest.fn(),
};

describe("ApmeAbbenaySettingsTab", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockApmeApi.getPortalSettings.mockResolvedValue({
      enableAi: true,
      publishViaGateway: true,
      defaultAiModelId: undefined,
    });
    mockApmeApi.getAiStatus.mockResolvedValue({
      enableAi: true,
      connected: true,
      modelCount: 1,
    });
    mockApmeApi.getAiModels.mockResolvedValue([
      { id: "model-a", provider: "test", name: "Model A" },
    ]);
    mockApmeApi.getAiProviders.mockResolvedValue([]);
    mockApmeApi.getAiEngines.mockResolvedValue([
      {
        id: "openrouter",
        requires_key: true,
        default_base_url: "",
        default_env_var: "",
      },
    ]);
    mockApmeApi.updatePortalSettings.mockResolvedValue({
      enableAi: false,
      publishViaGateway: true,
      defaultAiModelId: "model-a",
    });
  });

  const renderTab = () =>
    renderInTestApp(
      <ThemeProvider theme={theme}>
        <TestApiProvider apis={[[apmeApiRef, mockApmeApi]]}>
          <ApmeAbbenaySettingsTab />
        </TestApiProvider>
      </ThemeProvider>,
    );

  it("renders connection status and saves default model (enableAi toggled in Quality settings)", async () => {
    renderTab();

    expect(
      await screen.findByText(/Connected — 1 inference model available/i),
    ).toBeInTheDocument();

    expect(
      screen.getByText(/set in Git Repositories → Quality settings/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockApmeApi.updatePortalSettings).toHaveBeenCalledWith({
        defaultAiModelId: "model-a",
      });
    });

    expect(
      await screen.findByText("Abbenay AI settings saved."),
    ).toBeInTheDocument();
  });
});
