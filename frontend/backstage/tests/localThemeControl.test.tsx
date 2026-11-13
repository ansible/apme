import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Dialog, DialogBody, DialogHeader } from "@backstage/ui";
import { LocalThemeControl } from "../../../containers/rhdh/plugin/src/LocalThemeControl";
import { LocalThemeProvider } from "../../../containers/rhdh/plugin/src/LocalThemeProvider";
const { getApi } = vi.hoisted(() => ({ getApi: vi.fn() }));
vi.mock("@backstage/core-plugin-api", async (original) => ({
  ...(await original<object>()),
  useApi: getApi,
}));

function hostTheme(selectedId: string | undefined = "custom-night") {
  let selected: string | undefined = selectedId;
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
  return { setActiveThemeId, listeners };
}

it("selects installed custom light/dark themes and restores the host's system preference", () => {
  const { setActiveThemeId, listeners } = hostTheme();
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

it.each([null, "light"])(
  "applies host and Auto themes to a real BUI dialog and restores %s on unmount",
  (baseline) => {
    const root = document.documentElement;
    const originalMode = root.getAttribute("data-theme-mode");
    if (baseline === null) root.removeAttribute("data-theme-mode");
    else root.setAttribute("data-theme-mode", baseline);
    const { listeners } = hostTheme();
    let systemDark = false;
    const systemListeners = new Set<() => void>();
    const media = {
      get matches() {
        return systemDark;
      },
      media: "(prefers-color-scheme: dark)",
      addEventListener: (_event: string, listener: () => void) => {
        systemListeners.add(listener);
      },
      removeEventListener: (_event: string, listener: () => void) => {
        systemListeners.delete(listener);
      },
    } as unknown as MediaQueryList;
    const originalMatchMedia = window.matchMedia;
    const matchMedia = vi
      .spyOn(window, "matchMedia")
      .mockImplementation((query) =>
        query === media.media ? media : originalMatchMedia(query),
      );
    const view = render(
      <StrictMode>
        <LocalThemeProvider>
          <LocalThemeControl />
          <Dialog isOpen>
            <DialogHeader>Theme preview</DialogHeader>
            <DialogBody>Portaled content</DialogBody>
          </Dialog>
        </LocalThemeProvider>
      </StrictMode>,
    );
    try {
      const dialog = screen.getByRole("dialog", { name: "Theme preview" });
      expect(view.container.contains(dialog)).toBe(false);
      const expectMode = (mode: string) => {
        // BUI's native CSS selector must reach both inline and body-portal UI.
        expect(
          dialog.closest("[data-theme-mode]")?.getAttribute("data-theme-mode"),
        ).toBe(mode);
        expect(
          view.container
            .closest("[data-theme-mode]")
            ?.getAttribute("data-theme-mode"),
        ).toBe(mode);
      };
      const select = screen.getByRole("combobox", {
        name: "Color theme",
        hidden: true,
      });
      expectMode("dark");
      fireEvent.change(select, { target: { value: "light" } });
      expectMode("light");
      act(() => {
        systemDark = true;
        for (const listener of systemListeners) listener();
      });
      expectMode("light");
      fireEvent.change(select, { target: { value: "system" } });
      expectMode("dark");
      act(() => {
        systemDark = false;
        for (const listener of systemListeners) listener();
      });
      expectMode("light");
      view.unmount();
      expect(root.getAttribute("data-theme-mode")).toBe(baseline);
      expect(listeners.size).toBe(0);
      expect(systemListeners.size).toBe(0);
    } finally {
      view.unmount();
      matchMedia.mockRestore();
      if (originalMode === null) root.removeAttribute("data-theme-mode");
      else root.setAttribute("data-theme-mode", originalMode);
    }
  },
);
