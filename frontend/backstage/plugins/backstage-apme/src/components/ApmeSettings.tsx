/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useState } from "react";
import { Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { ApmeGalaxySettings } from "./ApmeGalaxySettings";
import { ApmeModelSettings } from "./ApmeModelSettings";
import { ApmeAiProviders } from "./ApmeAiProviders";

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
  const [modelsRevision, setModelsRevision] = useState(0);
  return (
    <Flex direction="column" gap="6">
      <Text as="h2" variant="title-medium">
        Settings
      </Text>
      <ApmeAiProviders
        adapter={adapter}
        canView={canViewSettings}
        canManage={canManageSettings}
        onChanged={() => setModelsRevision((value) => value + 1)}
      />
      <ApmeModelSettings key={modelsRevision} adapter={adapter} />
      <ApmeGalaxySettings
        adapter={adapter}
        canView={canViewSettings}
        canManage={canManageSettings}
        supportsCertificateSettings={supportsGalaxyCertificateSettings}
      />
    </Flex>
  );
}
