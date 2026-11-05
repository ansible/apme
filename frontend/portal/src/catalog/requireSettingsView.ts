import {
  HttpAuthService,
  PermissionsService,
} from "@backstage/backend-plugin-api";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { ansibleSettingsViewPermission } from "@ansible/backstage-rhaap-common/permissions";
import type { RequestHandler } from "express";

export function createRequireSettingsViewMiddleware(deps: {
  httpAuth: HttpAuthService;
  permissions: PermissionsService;
}): RequestHandler {
  const { httpAuth, permissions } = deps;
  return async (req, res, next) => {
    const credentials = await httpAuth.credentials(
      req as unknown as Parameters<HttpAuthService["credentials"]>[0],
      { allow: ["user"] },
    );
    const [decision] = await permissions.authorize(
      [{ permission: ansibleSettingsViewPermission, resourceRef: "apme" }],
      { credentials },
    );
    if (decision.result === AuthorizeResult.ALLOW) {
      next();
    } else {
      res.status(403).json({
        error:
          "Forbidden: ansible.settings.view permission required for capability apme",
      });
    }
  };
}
