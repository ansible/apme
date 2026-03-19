import {
  createApiFactory,
  createPlugin,
  createRoutableExtension,
  discoveryApiRef,
  identityApiRef,
} from '@backstage/core-plugin-api';
import { rootRouteRef } from './routes';
import { apmeApiRef } from './api/types';
import { BackstageApmeClient } from './api/ApmeClient';

export const apmePlugin = createPlugin({
  id: 'apme',
  routes: {
    root: rootRouteRef,
  },
  apis: [
    createApiFactory({
      api: apmeApiRef,
      deps: {
        discoveryApi: discoveryApiRef,
        identityApi: identityApiRef,
      },
      factory: ({ discoveryApi, identityApi }) =>
        BackstageApmeClient.create({ discoveryApi, identityApi }),
    }),
  ],
});

export const ApmePage = apmePlugin.provide(
  createRoutableExtension({
    name: 'ApmePage',
    component: () =>
      import('./components/ApmeRouter').then((m) => m.ApmeRouter),
    mountPoint: rootRouteRef,
  }),
);

export const EntityApmeCard = apmePlugin.provide(
  createRoutableExtension({
    name: 'EntityApmeCard',
    component: () =>
      import('./components/EntityApmeCard/EntityApmeCard').then(
        (m) => m.EntityApmeCard,
      ),
    mountPoint: rootRouteRef,
  }),
);
