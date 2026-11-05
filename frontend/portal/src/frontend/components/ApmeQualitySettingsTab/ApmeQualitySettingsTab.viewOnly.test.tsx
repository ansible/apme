/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import { configApiRef } from "@backstage/core-plugin-api";
import { apmeApiRef } from "../../api";
import { ApmeQualitySettingsTab } from "./ApmeQualitySettingsTab";

jest.mock("@backstage/plugin-permission-react", () => ({
  RequirePermission: (props: {
    permission: { name: string };
    children: React.ReactNode;
    errorPage?: React.ReactNode;
  }) => {
    if (props.permission.name === "ansible.settings.edit") {
      return <>{props.errorPage ?? null}</>;
    }
    return <>{props.children}</>;
  },
  usePermission: () => ({ loading: false, allowed: false }),
}));

describe("ApmeQualitySettingsTab (view-only)", () => {
  it("shows read-only AI status and hides providers when edit is denied", async () => {
    const getPortalSettings = jest.fn().mockResolvedValue({
      enableAi: true,
      publishViaGateway: true,
      targetAnsibleCoreVersion: "2.16",
    });

    const configApi = {
      getOptionalString: () => undefined,
      getOptionalBoolean: (key: string) =>
        key === "ansible.apme.enabled" ? true : undefined,
    };

    render(
      <TestApiProvider
        apis={[
          [
            apmeApiRef,
            {
              getPortalSettings,
              updatePortalSettings: jest.fn(),
              getAiProviders: jest.fn().mockResolvedValue([]),
              getAiStatus: jest.fn().mockResolvedValue({
                enableAi: true,
                connected: false,
                modelCount: 0,
              }),
            },
          ],
          [configApiRef, configApi],
        ]}
      >
        <ApmeQualitySettingsTab />
      </TestApiProvider>,
    );

    expect(await screen.findByText("Quality settings")).toBeInTheDocument();
    expect(
      screen.getByText(/AI-assisted remediation:\s*enabled \(read-only\)/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /AI-assisted remediation/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("AI providers")).not.toBeInTheDocument();
  });
});
