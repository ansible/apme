/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  ApiBlueprint,
  createFrontendPlugin,
  createRouteRef,
  PageBlueprint,
  SubPageBlueprint,
} from "@backstage/frontend-plugin-api";
import { apmeHostApiFactory } from "@apme/backstage-plugin/host-api";
import "./host.css";

const projectsRouteRef = createRouteRef();
const projectsIcon = (
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

const transport = ApiBlueprint.make({
  params: (defineParams) => defineParams(apmeHostApiFactory),
});

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
  extensions: [transport, page, settings],
});
