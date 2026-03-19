/**
 * Express router that proxies all requests to the APME gateway.
 *
 * Mounted at /api/apme in the Backstage backend.  Strips the /api/apme
 * prefix so /api/apme/api/v1/scans -> http://gateway:8080/api/v1/scans.
 */

import { Router } from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';

export interface RouterOptions {
  gatewayUrl: string;
}

export function createRouter(options: RouterOptions): Router {
  const router = Router();

  router.use(
    '/',
    createProxyMiddleware({
      target: options.gatewayUrl,
      changeOrigin: true,
      pathRewrite: { '^/api/apme': '' },
      ws: true,
    }),
  );

  return router;
}
