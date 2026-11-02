/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  createFrontendPlugin,
  PageBlueprint,
  SubPageBlueprint,
} from "@backstage/frontend-plugin-api";
import {
  apmeApi,
  projectsRouteRef,
  projectsIcon,
} from "@apme/backstage-plugin/alpha";
import "./host.css";

const page = PageBlueprint.make({
  params: {
    path: "/apme",
    title: "Projects",
    routeRef: projectsRouteRef,
    icon: projectsIcon,
    loader: () => import("./page").then((module) => <module.LocalApmePage />),
  },
});

const settings = SubPageBlueprint.make({
  name: "settings",
  attachTo: { id: "page:user-settings", input: "pages" },
  params: {
    path: "apme",
    title: "APME",
    loader: () =>
      import("./settings").then((module) => <module.LocalApmeSettings />),
  },
});

/** Personal RHDH entry point; Portal composes the standard package with its RBAC. */
export default createFrontendPlugin({
  pluginId: "apme",
  title: "Projects",
  icon: projectsIcon,
  routes: { root: projectsRouteRef },
  extensions: [apmeApi, page, settings],
});
