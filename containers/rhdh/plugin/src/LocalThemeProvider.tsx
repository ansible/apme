/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { PropsWithChildren } from "react";
import { appThemeApiRef, useApi } from "@backstage/core-plugin-api";
import { BUIProvider } from "@backstage/ui";

// BUI tokens must also reach dialogs portaled to document.body.
let ownerCount = 0;
let baselineMode: string | null = null;

export function LocalThemeProvider({ children }: PropsWithChildren) {
  useLocalThemeMode();
  return <BUIProvider>{children}</BUIProvider>;
}

function useLocalThemeMode(): void {
  const themeApi = useApi(appThemeApiRef);
  const systemTheme = useMemo(
    () => window.matchMedia("(prefers-color-scheme: dark)"),
    [],
  );
  const subscribe = useCallback(
    (notify: () => void) => {
      const subscription = themeApi.activeThemeId$().subscribe(notify);
      systemTheme.addEventListener("change", notify);
      return () => {
        subscription.unsubscribe();
        systemTheme.removeEventListener("change", notify);
      };
    },
    [themeApi, systemTheme],
  );
  const getSnapshot = useCallback(() => {
    // Match Backstage's AppThemeProvider resolution, including Auto mode and
    // custom host theme IDs, without adding a dependency on legacy MUI.
    const themes = themeApi.getInstalledThemes();
    const selected = themes.find(
      (theme) => theme.id === themeApi.getActiveThemeId(),
    );
    const preferred = systemTheme.matches
      ? themes.find((theme) => theme.variant === "dark")
      : undefined;
    const theme =
      selected ??
      preferred ??
      themes.find((t) => t.variant === "light") ??
      themes[0];
    return theme?.variant === "dark";
  }, [themeApi, systemTheme]);
  const isDark = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    const root = document.documentElement;
    if (ownerCount === 0) {
      baselineMode = root.getAttribute("data-theme-mode");
    }
    ownerCount += 1;
    root.setAttribute("data-theme-mode", isDark ? "dark" : "light");

    return () => {
      ownerCount -= 1;
      if (ownerCount === 0) {
        if (baselineMode === null) root.removeAttribute("data-theme-mode");
        else root.setAttribute("data-theme-mode", baselineMode);
      }
    };
  }, [isDark]);
}
