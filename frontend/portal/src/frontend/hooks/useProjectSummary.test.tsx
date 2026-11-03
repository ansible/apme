import { act, renderHook } from "@testing-library/react";
import type { Project } from "../../common/types";
import { useProjectSummary } from "./useProjectSummary";

const record = (scanCount: number, status?: string) =>
  ({
    id: "project",
    scan_count: scanCount,
    active_operation: status ? { status } : undefined,
  }) as Project;

describe("APME project summary polling", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());
  const settle = () =>
    act(async () => {
      await Promise.resolve();
    });

  it("refreshes after a background check and stops at completion", async () => {
    const api = {
      getProject: jest
        .fn()
        .mockResolvedValueOnce(record(2, "scanning"))
        .mockResolvedValueOnce(record(3)),
    };
    const { result } = renderHook(() =>
      useProjectSummary(api, "project", false),
    );
    await settle();
    expect(result.current.project?.scan_count).toBe(2);
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });
    expect(result.current.project?.scan_count).toBe(3);
    await act(async () => {
      jest.advanceTimersByTime(9000);
    });
    expect(api.getProject).toHaveBeenCalledTimes(2);
  });

  it("does not keep polling an idle project", async () => {
    const api = { getProject: jest.fn().mockResolvedValue(record(3)) };
    renderHook(() => useProjectSummary(api, "project", false));
    await settle();
    await act(async () => {
      jest.advanceTimersByTime(9000);
    });
    expect(api.getProject).toHaveBeenCalledTimes(1);
  });

  it("cleans up active polling on unmount", async () => {
    const api = {
      getProject: jest.fn().mockResolvedValue(record(2, "scanning")),
    };
    const { unmount } = renderHook(() =>
      useProjectSummary(api, "project", false),
    );
    await settle();
    unmount();
    await act(async () => {
      jest.advanceTimersByTime(9000);
    });
    expect(api.getProject).toHaveBeenCalledTimes(1);
  });

  it("exposes transport failures and does not treat them as successful results", async () => {
    const failure = new Error("Gateway unavailable");
    const api = { getProject: jest.fn().mockRejectedValue(failure) };
    const { result } = renderHook(() =>
      useProjectSummary(api, "project", false),
    );
    await settle();
    expect(result.current.error).toBe(failure);
    expect(result.current.project).toBeNull();
  });
});
