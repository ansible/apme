import { act, renderHook, waitFor } from "@testing-library/react";
import { useLoad } from "../plugins/backstage-apme/src/components/useLoad";

it("does not send a queued data request after its view has unmounted", async () => {
  const load = vi.fn(async () => "data");
  const view = renderHook(() => useLoad(load));
  view.unmount();
  await act(async () => {});
  expect(load).not.toHaveBeenCalled();
});

it("keeps the current value mounted while refreshing the same resource", async () => {
  let finishRefresh!: (value: string) => void;
  const load = vi
    .fn<(_: AbortSignal) => Promise<string>>()
    .mockResolvedValueOnce("loaded")
    .mockImplementationOnce(
      () => new Promise<string>((resolve) => (finishRefresh = resolve)),
    );
  const view = renderHook(() => useLoad(load));
  await act(async () => {});
  expect(view.result.current.value).toBe("loaded");

  act(() => view.result.current.reload());
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(view.result.current.loading).toBe(true);
  expect(view.result.current.value).toBe("loaded");
  expect(view.result.current.error).toBeUndefined();

  await act(async () => finishRefresh("updated"));
  expect(view.result.current.value).toBe("updated");
  expect(view.result.current.loading).toBe(false);
});

it("clears a resource value when loading is disabled", async () => {
  const load = vi.fn(async () => "loaded");
  const view = renderHook(({ enabled }) => useLoad(load, enabled), {
    initialProps: { enabled: true },
  });
  await act(async () => {});
  expect(view.result.current.value).toBe("loaded");

  view.rerender({ enabled: false });
  expect(view.result.current.value).toBeUndefined();
  expect(view.result.current.loading).toBe(false);
});
