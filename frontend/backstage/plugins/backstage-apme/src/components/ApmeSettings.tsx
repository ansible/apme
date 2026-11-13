/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { Alert, Box, Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { ApmeGitRequirementsSettings } from "./ApmeGitRequirementsSettings";
import { ApmeGalaxySettings } from "./ApmeGalaxySettings";
import { ApmeModelSettings } from "./ApmeModelSettings";
import { ApmeAiProviders } from "./ApmeAiProviders";
import { apmeHostApiRef } from "../api/apmeApi";
import { SectionHeader } from "./layout";
import { useLoad } from "./useLoad";

/** Global APME settings for a host-provided settings route or section. */
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
    <Box className="apme-ui apme-page">
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
    <Flex className="apme-ui apme-settings" direction="column" gap="4">
      <SectionHeader
        title="Settings"
        description="Manage AI providers, model preferences, Galaxy access and Git requirements."
      />
      <div className="apme-panel">
        <ApmeAiProviders
          adapter={adapter}
          canView={canViewSettings}
          canManage={canManageSettings}
          onChanged={() => setModelsRevision((value) => value + 1)}
        />
      </div>
      <div className="apme-panel">
        <ApmeModelSettings key={modelsRevision} adapter={adapter} />
      </div>
      <div className="apme-panel">
        <ApmeGalaxySettings
          adapter={adapter}
          canView={canViewSettings}
          canManage={canManageSettings}
          supportsCertificateSettings={supportsGalaxyCertificateSettings}
        />
      </div>
      <div className="apme-panel">
        <ApmeGitRequirementsSettings
          adapter={adapter}
          canView={canViewSettings}
          canManage={canManageSettings}
        />
      </div>
    </Flex>
  );
}
