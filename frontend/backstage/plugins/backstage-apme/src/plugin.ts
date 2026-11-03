/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  createPlugin,
  createRouteRef,
  createRoutableExtension,
} from "@backstage/core-plugin-api";
import { apmeHostApiFactory } from "./api/apmeApi";

export const rootRouteRef = createRouteRef({ id: "apme" });

export const apmePlugin = createPlugin({
  id: "apme",
  routes: { root: rootRouteRef },
  apis: [apmeHostApiFactory],
});

export const ApmePage = apmePlugin.provide(
  createRoutableExtension({
    name: "ApmePage",
    mountPoint: rootRouteRef,
    component: () =>
      import("./components/ApmePage").then((m) => m.ApmePageContent),
  }),
);
