/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  ApiBlueprint,
  PageBlueprint,
  createFrontendPlugin,
  createRouteRef,
} from "@backstage/frontend-plugin-api";
import { apmeHostApiFactory } from "./api/apmeApi";

export const projectsRouteRef = createRouteRef();
export const projectsIcon = (
  <svg
    viewBox="0 0 24 24"
    width="24"
    height="24"
    fill="currentColor"
    aria-hidden
  >
    <path d="M3 4h7l2 2h9a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm1 4v11h16V8h-8.83l-2-2H4v2Z" />
  </svg>
);

export const apmeApi = ApiBlueprint.make({
  params: (defineParams) => defineParams(apmeHostApiFactory),
});
export const projectsPage = PageBlueprint.make({
  params: {
    path: "/apme",
    title: "Projects",
    icon: projectsIcon,
    routeRef: projectsRouteRef,
    loader: () =>
      import("./components/ApmePage").then((m) => <m.ApmePageContent />),
  },
});

/** Standard NFS integration. Portal's SDK surfaces live in @apme/portal-plugin. */
export default createFrontendPlugin({
  pluginId: "apme",
  title: "Projects",
  icon: projectsIcon,
  routes: { root: projectsRouteRef },
  extensions: [apmeApi, projectsPage],
});
