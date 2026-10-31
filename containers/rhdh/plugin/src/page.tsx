/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { Navigate, useSearchParams } from "react-router-dom";
import { ApmePageContent } from "@apme/backstage-plugin/page";
import { LocalThemeProvider } from "./LocalThemeProvider";

export function LocalApmePage() {
  const [params] = useSearchParams();
  if (params.get("view") === "settings") {
    return <Navigate to="/settings/apme" replace />;
  }
  return (
    <LocalThemeProvider>
      <ApmePageContent showGlobalSettings={false} />
    </LocalThemeProvider>
  );
}
