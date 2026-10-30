import { act, renderHook } from "@testing-library/react";
import { useLoad } from "../plugins/backstage-apme/src/components/useLoad";

it("does not send a queued data request after its view has unmounted", async () => {
  const load = vi.fn(async () => "data");
  const view = renderHook(() => useLoad(load));
  view.unmount();
  await act(async () => {});
  expect(load).not.toHaveBeenCalled();
});
