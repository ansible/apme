/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useSyncExternalStore } from "react";
import { appThemeApiRef, useApi } from "@backstage/core-plugin-api";

/** Use the host's persisted preference, including its installed custom themes. */
export function LocalThemeControl() {
  const themeApi = useApi(appThemeApiRef);
  const subscribe = useCallback(
    (notify: () => void) => {
      const subscription = themeApi.activeThemeId$().subscribe(notify);
      return () => subscription.unsubscribe();
    },
    [themeApi],
  );
  const selected = useSyncExternalStore(subscribe, () =>
    themeApi.getActiveThemeId(),
  );
  const themes = themeApi.getInstalledThemes();
  const active = themes.find((theme) => theme.id === selected);
  return (
    <div className="apme-local-theme-control">
      <label htmlFor="apme-color-theme">Color theme</label>
      <select
        id="apme-color-theme"
        value={active?.variant ?? "system"}
        onChange={(event) => {
          const value = event.target.value;
          if (value === "system") themeApi.setActiveThemeId(undefined);
          else {
            const theme = themes.find(
              (candidate) => candidate.variant === value,
            );
            if (theme) themeApi.setActiveThemeId(theme.id);
          }
        }}
      >
        <option value="system">System</option>
        {themes.some((theme) => theme.variant === "light") && (
          <option value="light">Light</option>
        )}
        {themes.some((theme) => theme.variant === "dark") && (
          <option value="dark">Dark</option>
        )}
      </select>
    </div>
  );
}
