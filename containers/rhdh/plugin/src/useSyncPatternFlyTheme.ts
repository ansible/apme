/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { appThemeApiRef, useApi } from "@backstage/core-plugin-api";

// Adapted from the original backstage-apme plugin's hook of the same name.
// PatternFly's dark tokens require this class on :root, including for overlays.
const darkClass = "pf-v6-theme-dark";
let ownerCount = 0;
let baselineHadClass = false;

export function useSyncPatternFlyTheme(): void {
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
      baselineHadClass = root.classList.contains(darkClass);
    }
    ownerCount += 1;
    root.classList.toggle(darkClass, isDark);

    return () => {
      ownerCount -= 1;
      if (ownerCount === 0) {
        root.classList.toggle(darkClass, baselineHadClass);
      }
    };
  }, [isDark]);
}
