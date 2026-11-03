import { fireEvent, render, screen } from "@testing-library/react";
import { LocalThemeControl } from "../../../containers/rhdh/plugin/src/LocalThemeControl";
const { getApi } = vi.hoisted(() => ({ getApi: vi.fn() }));
vi.mock("@backstage/core-plugin-api", async (original) => ({
  ...(await original<object>()),
  useApi: getApi,
}));

it("selects installed custom light/dark themes and restores the host's system preference", () => {
  let selected: string | undefined = "custom-night";
  const listeners = new Set<() => void>();
  const setActiveThemeId = vi.fn((id?: string) => {
    selected = id;
    for (const listener of listeners) listener();
  });
  getApi.mockReturnValue({
    getInstalledThemes: () => [
      { id: "custom-day", variant: "light" },
      { id: "custom-night", variant: "dark" },
    ],
    getActiveThemeId: () => selected,
    setActiveThemeId,
    activeThemeId$: () => ({
      subscribe: (listener: () => void) => {
        listeners.add(listener);
        return { unsubscribe: () => listeners.delete(listener) };
      },
    }),
  });
  const view = render(<LocalThemeControl />);
  const select = screen.getByRole("combobox", { name: "Color theme" });
  fireEvent.change(select, { target: { value: "light" } });
  expect(setActiveThemeId).toHaveBeenLastCalledWith("custom-day");
  expect((select as HTMLSelectElement).value).toBe("light");
  fireEvent.change(select, { target: { value: "dark" } });
  expect(setActiveThemeId).toHaveBeenLastCalledWith("custom-night");
  fireEvent.change(select, { target: { value: "system" } });
  expect(setActiveThemeId).toHaveBeenLastCalledWith(undefined);
  view.unmount();
  expect(listeners.size).toBe(0);
});
