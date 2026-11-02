/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { Navigate, useSearchParams } from "react-router-dom";
import { ApmePageContent } from "@apme/backstage-plugin/page";
import { LocalThemeProvider } from "./LocalThemeProvider";
import { sectionPath } from "./sections";

export function LocalApmePage({
  section,
}: {
  section: "repositories" | "rules" | "health" | "upload";
}) {
  const [params] = useSearchParams();
  const legacyView = params.get("view");
  const path = legacyView && sectionPath(legacyView);
  if (path) {
    const next = new URLSearchParams(params);
    next.delete("view");
    return (
      <Navigate to={{ pathname: path, search: next.toString() }} replace />
    );
  }
  return (
    <LocalThemeProvider>
      <ApmePageContent
        section={section}
        showSectionNavigation={false}
        showGlobalSettings={false}
      />
    </LocalThemeProvider>
  );
}
