/**
 * APME backend plugin for Backstage.
 *
 * Registers a proxy route that forwards /api/apme/* requests to the
 * APME gateway.  Configure the gateway URL in app-config.yaml:
 *
 *   apme:
 *     gatewayUrl: http://localhost:8080
 */

import {
  coreServices,
  createBackendPlugin,
} from '@backstage/backend-plugin-api';
import { createRouter } from './router';

export const apmeBackendPlugin = createBackendPlugin({
  pluginId: 'apme',
  register(env) {
    env.registerInit({
      deps: {
        httpRouter: coreServices.httpRouter,
        config: coreServices.rootConfig,
        logger: coreServices.logger,
      },
      async init({ httpRouter, config, logger }) {
        const gatewayUrl =
          config.getOptionalString('apme.gatewayUrl') ??
          'http://localhost:8080';

        logger.info(`APME backend plugin: proxying to ${gatewayUrl}`);
        httpRouter.use(createRouter({ gatewayUrl }));
      },
    });
  },
});
