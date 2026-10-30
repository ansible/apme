/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { ApmeGalaxySettings } from "./ApmeGalaxySettings";
import { ApmeModelSettings } from "./ApmeModelSettings";

export interface ApmeSettingsProps {
  adapter: ApmeApiAdapter;
  canViewSettings?: boolean;
  canManageSettings?: boolean;
  supportsGalaxyCertificateSettings?: boolean;
}

export function ApmeSettings({
  adapter,
  canViewSettings,
  canManageSettings,
  supportsGalaxyCertificateSettings,
}: ApmeSettingsProps) {
  return (
    <Flex direction="column" gap="6">
      <Text as="h2" variant="title-medium">
        Settings
      </Text>
      <ApmeModelSettings adapter={adapter} />
      <ApmeGalaxySettings
        adapter={adapter}
        canView={canViewSettings}
        canManage={canManageSettings}
        supportsCertificateSettings={supportsGalaxyCertificateSettings}
      />
    </Flex>
  );
}
