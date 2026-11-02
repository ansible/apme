import {
  HttpAuthService,
  PermissionsService,
} from "@backstage/backend-plugin-api";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { ansibleSettingsEditPermission } from "@ansible/backstage-rhaap-common/permissions";

export function createRequireSettingsManageMiddleware(deps: {
  httpAuth: HttpAuthService;
  permissions: PermissionsService;
}): any {
  const { httpAuth, permissions } = deps;
  return async (req: any, res: any, next: any) => {
    const credentials = await httpAuth.credentials(
      req as unknown as Parameters<HttpAuthService["credentials"]>[0],
      { allow: ["user"] },
    );
    const [decision] = await permissions.authorize(
      [{ permission: ansibleSettingsEditPermission, resourceRef: "apme" }],
      { credentials },
    );
    if (decision.result === AuthorizeResult.ALLOW) {
      next();
    } else {
      res.status(403).json({
        error:
          "Forbidden: ansible.settings.edit permission required for capability apme",
      });
    }
  };
}
