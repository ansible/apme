/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import { configApiRef } from "@backstage/core-plugin-api";
import { DEFAULT_APME_FEEDBACK_FORM_URL } from "../../constants/previewFeedback";
import { apmeApiRef } from "../../api";
import { ApmeQualitySettingsTab } from "./ApmeQualitySettingsTab";

jest.mock("@backstage/plugin-permission-react", () => ({
  RequirePermission: (props: any) => props.children,
  usePermission: () => ({ loading: false, allowed: true }),
}));

jest.mock("./ApmeAiProvidersSection", () => ({
  ApmeAiProvidersSection: ({ fillHeight }: { fillHeight?: boolean }) => (
    <div
      data-testid="ai-providers-section"
      data-fill-height={fillHeight ? "true" : "false"}
    >
      AI providers
    </div>
  ),
}));

describe("ApmeQualitySettingsTab", () => {
  const getPortalSettings = jest.fn();
  const updatePortalSettings = jest.fn();
  const getAiProviders = jest.fn();
  const getAiStatus = jest.fn();
  const configureAiProvider = jest.fn();
  const deleteAiProvider = jest.fn();

  const apmeApi = {
    getPortalSettings,
    updatePortalSettings,
    getAiProviders,
    getAiStatus,
    configureAiProvider,
    deleteAiProvider,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    getPortalSettings.mockResolvedValue({
      enableAi: true,
      publishViaGateway: true,
      targetAnsibleCoreVersion: "2.16",
      gatewayBaseUrl: "http://localhost:8080",
    });
    updatePortalSettings.mockResolvedValue({
      enableAi: true,
      publishViaGateway: true,
      targetAnsibleCoreVersion: "2.18",
      gatewayBaseUrl: "http://localhost:8080",
    });
    getAiProviders.mockResolvedValue([]);
    getAiStatus.mockResolvedValue({
      enableAi: true,
      connected: false,
      modelCount: 0,
    });
  });

  function renderTab() {
    const configApi = {
      getOptionalString: (key: string) =>
        key === "ansible.apme.enabled" ? undefined : undefined,
      getOptionalBoolean: (key: string) =>
        key === "ansible.apme.enabled" ? true : undefined,
    };
    return render(
      <TestApiProvider
        apis={[
          [apmeApiRef, apmeApi],
          [configApiRef, configApi],
        ]}
      >
        <ApmeQualitySettingsTab />
      </TestApiProvider>,
    );
  }

  it("loads current ansible-core target and AI toggle from portal settings", async () => {
    renderTab();
    expect(await screen.findByText("Quality settings")).toBeInTheDocument();
    expect(getPortalSettings).toHaveBeenCalled();
    expect(screen.getByLabelText(/target ansible-core/i)).toHaveTextContent(
      "ansible-core 2.16",
    );
    expect(screen.getByLabelText("APME service URL")).toHaveValue(
      "http://localhost:8080",
    );
    expect(
      screen.getByRole("checkbox", { name: /AI-assisted remediation/i }),
    ).toBeChecked();
  });

  it("saves ansible-core target, service URL, and enableAi via updatePortalSettings", async () => {
    renderTab();
    await screen.findByText("Quality settings");

    const select = screen.getByLabelText(/target ansible-core/i);
    fireEvent.mouseDown(select);
    fireEvent.click(
      await screen.findByRole("option", { name: "ansible-core 2.18" }),
    );

    const save = screen.getByRole("button", { name: /^save$/i });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => {
      expect(updatePortalSettings).toHaveBeenCalledWith({
        targetAnsibleCoreVersion: "2.18",
        gatewayBaseUrl: "http://localhost:8080",
        enableAi: true,
      });
    });
    expect(
      await screen.findByText("Quality defaults saved."),
    ).toBeInTheDocument();
  });

  it("saves enableAi toggle via updatePortalSettings", async () => {
    updatePortalSettings.mockResolvedValue({
      enableAi: false,
      publishViaGateway: true,
      targetAnsibleCoreVersion: "2.16",
      gatewayBaseUrl: "http://localhost:8080",
    });
    renderTab();
    await screen.findByText("Quality settings");

    fireEvent.click(
      screen.getByRole("checkbox", { name: /AI-assisted remediation/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(updatePortalSettings).toHaveBeenCalledWith({
        targetAnsibleCoreVersion: "2.16",
        gatewayBaseUrl: "http://localhost:8080",
        enableAi: false,
      });
    });
  });

  it("saves an APME service URL via updatePortalSettings", async () => {
    renderTab();
    await screen.findByText("Quality settings");

    const urlField = screen.getByLabelText("APME service URL");
    fireEvent.change(urlField, {
      target: { value: "http://host.containers.internal:8080" },
    });

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(updatePortalSettings).toHaveBeenCalledWith({
        targetAnsibleCoreVersion: "2.16",
        gatewayBaseUrl: "http://host.containers.internal:8080",
        enableAi: true,
      });
    });
  });

  it("shows an error panel when load fails", async () => {
    getPortalSettings.mockRejectedValueOnce(new Error("settings unavailable"));
    renderTab();
    expect(
      await screen.findByText(/settings unavailable/i),
    ).toBeInTheDocument();
  });

  it("renders the AI providers card alongside quality settings", async () => {
    renderTab();
    expect(await screen.findByText("Quality settings")).toBeInTheDocument();
    expect(await screen.findByText("AI providers")).toBeInTheDocument();
  });

  it("passes fillHeight to AI providers section when edit permission is granted", async () => {
    renderTab();
    await screen.findByText("Quality settings");

    expect(screen.getByTestId("ai-providers-section")).toHaveAttribute(
      "data-fill-height",
      "true",
    );
  });

  it("uses stretch layout classes on the quality settings card when edit is allowed", async () => {
    const { container } = renderTab();
    await screen.findByText("Quality settings");

    const qualityCard = screen
      .getByText("Quality settings")
      .closest(".MuiCard-root");
    expect(qualityCard?.className).toEqual(
      expect.stringMatching(/settingsCard/),
    );

    const cardContent = qualityCard?.querySelector(".MuiCardContent-root");
    expect(cardContent?.className).toEqual(
      expect.stringMatching(/settingsCardContent/),
    );

    const saveButton = screen.getByRole("button", { name: /^save$/i });
    let footer: HTMLElement | null = saveButton.parentElement;
    while (footer && !footer.className.match(/settingsFooter/)) {
      footer = footer.parentElement;
    }
    expect(footer?.className).toEqual(expect.stringMatching(/settingsFooter/));

    const gridItems = container.querySelectorAll(".MuiGrid-item");
    expect(gridItems.length).toBeGreaterThanOrEqual(2);
    expect(gridItems[0].className).toEqual(
      expect.stringMatching(/settingsColumn/),
    );
    expect(gridItems[1].className).toEqual(
      expect.stringMatching(/settingsColumn/),
    );
  });

  it("does not render the Galaxy servers section", async () => {
    renderTab();
    expect(await screen.findByText("Quality settings")).toBeInTheDocument();
    expect(screen.queryByText("Galaxy servers")).not.toBeInTheDocument();
  });

  it("renders early-access preview and feedback link at the top", async () => {
    renderTab();
    const qualitySettings = await screen.findByText("Quality settings");
    const previewChip = screen.getByTestId("preview-chip");
    expect(qualitySettings).toBeInTheDocument();
    expect(previewChip).toHaveTextContent("Early access preview");
    const feedbackLink = screen.getByRole("link", {
      name: /Share your feedback/i,
    });
    expect(feedbackLink).toHaveAttribute(
      "href",
      DEFAULT_APME_FEEDBACK_FORM_URL,
    );
    expect(feedbackLink).toHaveAttribute("target", "_blank");
    expect(
      previewChip.compareDocumentPosition(qualitySettings) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
