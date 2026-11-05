import React, { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ApiProvider } from "@backstage/core-app-api";
import { ConfigReader } from "@backstage/config";
import type { ApiHolder, ApiRef } from "@backstage/core-plugin-api";
import { BUIProvider } from "@backstage/ui";
import {
  apmeHostApiFactory,
  apmeHostApiRef,
} from "../plugins/backstage-apme/src/api/apmeApi";
import "@backstage/ui/css/styles.css";
import "@patternfly/react-core/dist/styles/base.css";

const ApmePageContent = lazy(() =>
  import("../plugins/backstage-apme/src/components/ApmePage").then((m) => ({
    default: m.ApmePageContent,
  })),
);

const api = apmeHostApiFactory.factory({
  configApi: new ConfigReader({ apme: { apiBase: "/api/v1" } }),
  discoveryApi: { getBaseUrl: async () => window.location.origin },
  fetchApi: { fetch: (input, init) => globalThis.fetch(input, init) },
});
const apis: ApiHolder = {
  get<T>(ref: ApiRef<T>) {
    return ref.id === apmeHostApiRef.id ? (api as T) : undefined;
  },
};

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ApiProvider apis={apis}>
        <BUIProvider>
          <Suspense fallback={<p role="status">Loading APME…</p>}>
            <ApmePageContent />
          </Suspense>
        </BUIProvider>
      </ApiProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
