/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { createFrontendModule } from "@backstage/frontend-plugin-api";
import { NavContentBlueprint } from "@backstage/plugin-app-react";
import { LocalNavigation } from "./LocalNavigation";
import "./host.css";

/** Customize the personal host's navigation through its supported app slot. */
export default createFrontendModule({
  pluginId: "app",
  extensions: [
    NavContentBlueprint.make({
      params: { component: LocalNavigation },
    }),
  ],
});
