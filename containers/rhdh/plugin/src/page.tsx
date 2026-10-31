/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { BUIProvider } from "@backstage/ui";
import { ApmePageContent } from "@apme/backstage-plugin/page";
import { useSyncPatternFlyTheme } from "./useSyncPatternFlyTheme";
import "@backstage/ui/css/styles.css";
import "./theme.css";

export function LocalApmePage() {
  useSyncPatternFlyTheme();
  return (
    <BUIProvider>
      <ApmePageContent />
    </BUIProvider>
  );
}
