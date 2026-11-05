/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { Config } from "@backstage/config";
import { InputError, NotAllowedError } from "@backstage/errors";
import type { PortalOperations } from "@ansible/portal-plugin-node";
import {
  apmeSettingsGetOperation,
  apmeSettingsSaveOperation,
} from "../common/operations";
import { isApmeEnabled } from "../common/config";
import type { ApmePortalSettingsStore } from "./apmePortalSettingsStore";
import { createPortalSettingsService } from "./settingsService";

export function registerSettingsOperations(options: {
  operations: PortalOperations;
  rootConfig: Config;
  store: ApmePortalSettingsStore;
}) {
  const service = createPortalSettingsService(
    options.rootConfig,
    options.store,
  );
  for (const descriptor of [
    apmeSettingsGetOperation,
    apmeSettingsSaveOperation,
  ]) {
    options.operations.register({
      pluginId: "apme",
      descriptor,
      async handler({ subject, input }, context) {
        if (!isApmeEnabled(options.rootConfig))
          throw new NotAllowedError("APME is disabled");
        if (subject.entityRef !== context.userEntityRef)
          throw new InputError("Settings subject must be the current user");
        return descriptor.id === apmeSettingsGetOperation.id
          ? service.get()
          : service.save(input ?? {});
      },
    });
  }
}
