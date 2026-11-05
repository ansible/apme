/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  coreServices,
  createBackendPlugin,
} from "@backstage/backend-plugin-api";
import { createUploadSessionRouter } from "./uploadRouter.js";
export {
  createUploadSessionRouter,
  type UploadRouterOptions,
} from "./uploadRouter.js";

/** Personal Local profile; Portal mounts the router with its own authorization. */
export default createBackendPlugin({
  pluginId: "apme",
  register(env) {
    env.registerInit({
      deps: {
        httpRouter: coreServices.httpRouter,
        httpAuth: coreServices.httpAuth,
        config: coreServices.rootConfig,
        lifecycle: coreServices.lifecycle,
      },
      async init({ httpRouter, httpAuth, config, lifecycle }) {
        // Keep this out of the shared schema's required set, but fail at startup
        // when the optional Local transport itself is installed without config.
        const gatewayBaseUrl = config.getString("apme.gatewayBaseUrl");
        const transport = createUploadSessionRouter({
          authorize: async (req) => {
            const credentials = await httpAuth.credentials(req, {
              allow: ["user"],
            });
            return credentials.principal.userEntityRef;
          },
          gatewayBaseUrl: async () => gatewayBaseUrl,
        });
        httpRouter.use(transport.router);
        lifecycle.addShutdownHook(async () => transport.close());
      },
    });
  },
});
