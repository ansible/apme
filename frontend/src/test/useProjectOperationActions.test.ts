import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useProjectOperationActions } from "../hooks/useProjectOperationActions";

describe("useProjectOperationActions start payload", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ operation_id: "op-1" }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes interactive: true for remediate by default (caller)", async () => {
    const { result } = renderHook(() => useProjectOperationActions("proj-1"));
    await act(async () => {
      await result.current.start("remediate", {
        enable_ai: true,
        interactive: true,
      });
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/projects/proj-1/operation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "remediate",
          options: { enable_ai: true, interactive: true },
        }),
      }),
    );
  });

  it("passes abandon_working_set at the request top level", async () => {
    const { result } = renderHook(() => useProjectOperationActions("proj-1"));
    await act(async () => {
      await result.current.start("remediate", {
        interactive: true,
        abandon_working_set: true,
      });
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/projects/proj-1/operation",
      expect.objectContaining({
        body: JSON.stringify({
          action: "remediate",
          options: { interactive: true },
          abandon_working_set: true,
        }),
      }),
    );
  });
});
