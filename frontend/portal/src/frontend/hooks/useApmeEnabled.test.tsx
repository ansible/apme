import { act, renderHook, waitFor } from "@testing-library/react";

const mockConfigApi = {
  getOptionalBoolean: jest.fn().mockReturnValue(true),
};
const mockApmeApi = {
  getPortalSettings: jest.fn(),
};

jest.mock("@backstage/core-plugin-api", () => ({
  configApiRef: { id: "config" },
  useApi: (ref: { id: string }) =>
    ref.id === "plugin.apme.api" ? mockApmeApi : mockConfigApi,
}));

jest.mock("../api", () => ({
  apmeApiRef: { id: "plugin.apme.api" },
}));

import type { ApmePortalSettings } from "../../common/types";
import {
  invalidateApmePortalSettingsCache,
  useApmeAiEnabled,
} from "./useApmeEnabled";

const settings = (enableAi: boolean): ApmePortalSettings => ({
  enableAi,
  publishViaGateway: true,
});

describe("useApmeAiEnabled settings refresh", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    invalidateApmePortalSettingsCache();
  });

  it("refreshes mounted consumers when settings are saved", async () => {
    mockApmeApi.getPortalSettings
      .mockResolvedValueOnce(settings(false))
      .mockResolvedValueOnce(settings(true));
    const { result } = renderHook(() => useApmeAiEnabled());

    await waitFor(() =>
      expect(mockApmeApi.getPortalSettings).toHaveBeenCalledTimes(1),
    );
    await waitFor(() => expect(result.current).toBe(false));

    act(() => invalidateApmePortalSettingsCache());

    await waitFor(() => expect(result.current).toBe(true));
    expect(mockApmeApi.getPortalSettings).toHaveBeenCalledTimes(2);
  });
});
