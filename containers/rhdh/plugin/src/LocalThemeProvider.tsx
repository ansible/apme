/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { PropsWithChildren } from "react";
import { BUIProvider } from "@backstage/ui";
import { useSyncPatternFlyTheme } from "./useSyncPatternFlyTheme";
import "@backstage/ui/css/styles.css";
import "./theme.css";

export function LocalThemeProvider({ children }: PropsWithChildren) {
  useSyncPatternFlyTheme();
  return <BUIProvider>{children}</BUIProvider>;
}
