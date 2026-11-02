/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import {
  configApiRef,
  identityApiRef,
  useApiHolder,
} from "@backstage/core-plugin-api";
import { portalEntitlementsApiRef } from "@ansible/portal-extension-api";
import { withPortalGate } from "./PortalContributionGate";

jest.mock("@backstage/core-plugin-api", () => ({
  ...jest.requireActual("@backstage/core-plugin-api"),
  useApiHolder: jest.fn(),
}));

describe("catalog contribution access", () => {
  const check = jest.fn();
  const getIdentity = jest.fn();
  let enabled: boolean;
  beforeEach(() => {
    enabled = true;
    check.mockReset().mockResolvedValue({ allowed: true });
    getIdentity
      .mockReset()
      .mockResolvedValue({ userEntityRef: "user:default/alice" });
    const config = { getOptionalBoolean: () => enabled };
    const identity = { getBackstageIdentity: getIdentity };
    const entitlements = { check };
    jest.mocked(useApiHolder).mockReturnValue({
      get: (ref: unknown) => {
        if (ref === configApiRef) return config;
        if (ref === identityApiRef) return identity;
        if (ref === portalEntitlementsApiRef) return entitlements;
        return undefined;
      },
    } as any);
  });
  it("checks the signed-in subject before loading fleet data", async () => {
    const load = jest.fn(async () => <div>Fleet loaded</div>);
    render(await withPortalGate(load)());
    expect(await screen.findByText("Fleet loaded")).toBeInTheDocument();
    expect(check).toHaveBeenCalledWith("apme.scanning", "user:default/alice");
    expect(load).toHaveBeenCalledTimes(1);
  });
  it("does not load fleet data when the entitlement is denied", async () => {
    check.mockResolvedValue({ allowed: false });
    const load = jest.fn(async () => <div>Fleet loaded</div>);
    render(await withPortalGate(load)());
    await waitFor(() => expect(check).toHaveBeenCalled());
    expect(load).not.toHaveBeenCalled();
    expect(screen.queryByText("Fleet loaded")).not.toBeInTheDocument();
  });
  it("does not call the entitlement service or loader when disabled", async () => {
    enabled = false;
    const load = jest.fn(async () => <div>Fleet loaded</div>);
    render(await withPortalGate(load)());
    expect(getIdentity).not.toHaveBeenCalled();
    expect(check).not.toHaveBeenCalled();
    expect(load).not.toHaveBeenCalled();
  });
  it("allows settings without a scanning entitlement", async () => {
    check.mockResolvedValue({ allowed: false });
    render(await withPortalGate(async () => <div>Settings loaded</div>, [])());
    expect(await screen.findByText("Settings loaded")).toBeInTheDocument();
    expect(check).not.toHaveBeenCalled();
  });
});
