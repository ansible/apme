/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  createFrontendPlugin,
  PageBlueprint,
} from "@backstage/frontend-plugin-api";
import {
  apmeApi,
  projectsRouteRef,
  projectsIcon,
} from "@apme/backstage-plugin/alpha";
import "@backstage/ui/css/styles.css";
import "./theme.css";
import "@apme/backstage-plugin/styles.css";
import "./host.css";
import { loadLocalPage } from "./assetLoading";
import { Navigate } from "react-router-dom";
import { sections } from "./sections";

const pages = sections.map(({ name, section, title, path, routeRef, icon }) =>
  PageBlueprint.make({
    ...(name ? { name } : {}),
    params: {
      path,
      title,
      routeRef,
      icon,
      loader: () =>
        loadLocalPage(async () => {
          if (section === "settings") {
            const { LocalApmeSettings } = await import("./settings");
            return <LocalApmeSettings />;
          }
          const { LocalApmePage } = await import("./page");
          return <LocalApmePage section={section} />;
        }),
    },
  }),
);

// Preserve direct bookmarks without adding another Settings navigation entry.
const legacySettings = PageBlueprint.make({
  name: "legacy-settings",
  params: {
    path: "/settings/apme",
    loader: async () => <Navigate to="/apme/settings" replace />,
  },
});

/** Personal RHDH entry point; Portal composes the standard package with its RBAC. */
export default createFrontendPlugin({
  pluginId: "apme",
  title: "Projects",
  icon: projectsIcon,
  routes: { root: projectsRouteRef },
  extensions: [apmeApi, ...pages, legacySettings],
});
