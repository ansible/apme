/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import { useMemo } from "react";
import { useTheme } from "@material-ui/core";
import {
  getDependencySourceTokens,
  getDependencyViolationTokens,
  getCodePreviewTokens,
  getFixTypeColorTokens,
  getPreviewSurfaceTokens,
  getSeverityColorTokens,
  type ThemeMode,
} from "../../common/severity";

export function useApmeColorTokens() {
  const theme = useTheme();
  const mode: ThemeMode = theme.palette.type === "dark" ? "dark" : "light";

  return useMemo(
    () => ({
      mode,
      severity: getSeverityColorTokens(mode),
      fixType: getFixTypeColorTokens(mode),
      dependencySource: getDependencySourceTokens(mode),
      dependencyViolation: getDependencyViolationTokens(mode),
      codePreview: getCodePreviewTokens(mode),
      preview: getPreviewSurfaceTokens(mode),
    }),
    [mode],
  );
}
