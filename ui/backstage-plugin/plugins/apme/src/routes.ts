import { createRouteRef, createSubRouteRef } from '@backstage/core-plugin-api';

export const rootRouteRef = createRouteRef({ id: 'apme' });

export const scanDetailRouteRef = createSubRouteRef({
  id: 'apme/scan-detail',
  parent: rootRouteRef,
  path: '/scans/:scanId',
});

export const ruleDetailRouteRef = createSubRouteRef({
  id: 'apme/rule-detail',
  parent: rootRouteRef,
  path: '/rules/:ruleId',
});
