/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeSettingsPageContent } from "@apme/backstage-plugin/settings";
import { LocalThemeProvider } from "./LocalThemeProvider";

export function LocalApmeSettings() {
  return (
    <LocalThemeProvider>
      <ApmeSettingsPageContent />
    </LocalThemeProvider>
  );
}
