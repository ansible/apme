/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { Alert, Box, Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { ApmeGalaxySettings } from "./ApmeGalaxySettings";
import { ApmeModelSettings } from "./ApmeModelSettings";
import { ApmeAiProviders } from "./ApmeAiProviders";
import { apmeHostApiRef } from "../api/apmeApi";
import { useLoad } from "./useLoad";

/** Global APME settings, mounted in the host's existing Settings page. */
export function ApmeSettingsPageContent() {
  const hostApi = useApi(apmeHostApiRef);
  const load = useCallback(() => hostApi.getAdapter(), [hostApi]);
  const { value: adapter, error } = useLoad(load);
  if (error)
    return (
      <Alert
        status="danger"
        title="Could not connect to APME"
        description={error}
      />
    );
  if (!adapter) return <Text role="status">Connecting to APME…</Text>;
  return (
    <Box p="6">
      <ApmeSettings
        adapter={adapter}
        canViewSettings={hostApi.canViewSettings === true}
        canManageSettings={hostApi.canManageSettings === true}
        supportsGalaxyCertificateSettings={
          hostApi.supportsGalaxyCertificateSettings === true
        }
      />
    </Box>
  );
}

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
