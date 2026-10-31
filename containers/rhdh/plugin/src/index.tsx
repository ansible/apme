/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  ApiBlueprint,
  createFrontendPlugin,
  PageBlueprint,
} from "@backstage/frontend-plugin-api";
import { apmeHostApiFactory } from "@apme/backstage-plugin/host-api";

const transport = ApiBlueprint.make({
  params: (defineParams) => defineParams(apmeHostApiFactory),
});

const page = PageBlueprint.make({
  params: {
    path: "/apme",
    title: "Projects",
    loader: () => import("./page").then((module) => <module.LocalApmePage />),
  },
});

/** Personal RHDH entry point; Portal composes the standard package with its RBAC. */
export default createFrontendPlugin({
  pluginId: "apme",
  extensions: [transport, page],
});
