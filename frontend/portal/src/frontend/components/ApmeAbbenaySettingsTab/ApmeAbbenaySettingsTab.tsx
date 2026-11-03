/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useCallback, useEffect, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { Progress, ResponseErrorPanel } from "@backstage/core-components";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
} from "@patternfly/react-core";
import CheckCircleIcon from "@patternfly/react-icons/dist/esm/icons/check-circle-icon";
import ExclamationCircleIcon from "@patternfly/react-icons/dist/esm/icons/exclamation-circle-icon";
import "@patternfly/react-core/dist/styles/base.css";
import { AI_MODEL_STORAGE_KEY } from "@apme/ui-workflow";
import type { ApmePortalSettings } from "../../../common/types";
import { RequirePermission } from "@backstage/plugin-permission-react";
import { ansibleSettingsViewPermission } from "@ansible/backstage-rhaap-common/permissions";
import { apmeApiRef } from "../../api";
import { useSyncPatternFlyTheme } from "../../hooks/useSyncPatternFlyTheme";
import { ApmeAiProvidersSection } from "../ApmeQualitySettingsTab/ApmeAiProvidersSection";

type ApmeAiModelRow = { id: string; provider: string; name: string };

function connectionLabel(
  connected: boolean,
  modelCount: number,
  configuredModelCount?: number,
): string {
  if (connected && modelCount > 0) {
    return `Connected — ${modelCount} inference model${modelCount === 1 ? "" : "s"} available`;
  }
  if (connected) {
    const configured =
      configuredModelCount && configuredModelCount > 0
        ? ` (${configuredModelCount} configured in admin — not yet listed for inference)`
        : "";
    return `Abbenay reachable — no inference models listed${configured}`;
  }
  return "Disconnected — check Abbenay configuration on the APME service";
}

const ApmeAbbenaySettingsTabContent = () => {
  useSyncPatternFlyTheme();
  const apmeApi = useApi(apmeApiRef);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const [portalSettings, setPortalSettings] =
    useState<ApmePortalSettings | null>(null);
  const [models, setModels] = useState<ApmeAiModelRow[]>([]);
  const [connected, setConnected] = useState(false);
  const [modelCount, setModelCount] = useState(0);
  const [configuredModelCount, setConfiguredModelCount] = useState(0);

  const [enableAi, setEnableAi] = useState(false);
  const [selectedModelId, setSelectedModelId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [settings, status, modelList] = await Promise.all([
        apmeApi.getPortalSettings(),
        apmeApi.getAiStatus(),
        apmeApi.getAiModels(),
      ]);
      setPortalSettings(settings);
      setConnected(status.connected);
      setModelCount(status.modelCount);
      setConfiguredModelCount(status.configuredModelCount ?? 0);
      setModels(modelList);
      setEnableAi(settings.enableAi);
      const defaultId =
        settings.defaultAiModelId ??
        localStorage.getItem(AI_MODEL_STORAGE_KEY) ??
        modelList[0]?.id ??
        "";
      setSelectedModelId(defaultId);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, [apmeApi]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setSaveMessage(null);
    setError(null);
    try {
      const updated = await apmeApi.updatePortalSettings({
        defaultAiModelId: selectedModelId || null,
      });
      setPortalSettings(updated);
      if (selectedModelId) {
        localStorage.setItem(AI_MODEL_STORAGE_KEY, selectedModelId);
      }
      setSaveMessage("Abbenay AI settings saved.");
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <Progress />;
  }
  if (error && !portalSettings) {
    return <ResponseErrorPanel error={error} />;
  }

  return (
    <Card>
      <CardTitle>Abbenay AI</CardTitle>
      <CardBody>
        <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
          <FlexItem>
            <Flex
              gap={{ default: "gapSm" }}
              alignItems={{ default: "alignItemsCenter" }}
            >
              {connected ? (
                <CheckCircleIcon color="var(--pf-t--global--icon--color--status--success--default)" />
              ) : (
                <ExclamationCircleIcon color="var(--pf-t--global--icon--color--status--danger--default)" />
              )}
              <span>
                {connectionLabel(connected, modelCount, configuredModelCount)}
              </span>
            </Flex>
          </FlexItem>

          {error ? (
            <FlexItem>
              <Alert
                variant="danger"
                isInline
                title="Save failed"
                ouiaId="abbenay-save-error"
              >
                {error.message}
              </Alert>
            </FlexItem>
          ) : null}

          {saveMessage ? (
            <FlexItem>
              <Alert
                variant="success"
                isInline
                title={saveMessage}
                ouiaId="abbenay-save-ok"
              />
            </FlexItem>
          ) : null}

          <FlexItem>
            <div style={{ fontSize: 14 }}>
              AI-assisted remediation:{" "}
              <strong>{enableAi ? "enabled" : "disabled"}</strong>
              <span style={{ opacity: 0.7 }}>
                {" "}
                (set in Git Repositories → Quality settings)
              </span>
            </div>
          </FlexItem>

          <FlexItem>
            <label
              htmlFor="abbenay-default-model"
              style={{ display: "block", marginBottom: 4, fontWeight: 600 }}
            >
              Default AI model
            </label>
            {models.length === 0 ? (
              <div style={{ opacity: 0.7, fontSize: 14 }}>
                No inference models available — configure providers below, then
                ensure Abbenay lists models via ListAIModels.
              </div>
            ) : (
              <FormSelect
                id="abbenay-default-model"
                value={selectedModelId}
                onChange={(_e, value) => setSelectedModelId(value)}
                isDisabled={!enableAi}
                aria-label="Default AI model"
              >
                {models.map((model) => (
                  <FormSelectOption
                    key={model.id}
                    value={model.id}
                    label={`${model.name || model.id} (${model.provider})`}
                  />
                ))}
              </FormSelect>
            )}
          </FlexItem>

          <FlexItem>
            <Button
              variant="primary"
              isLoading={saving}
              isDisabled={
                saving || (enableAi && !selectedModelId && models.length > 0)
              }
              onClick={() => void handleSave()}
            >
              Save
            </Button>
          </FlexItem>

          <FlexItem>
            <ApmeAiProvidersSection />
          </FlexItem>
        </Flex>
      </CardBody>
    </Card>
  );
};

/** Git Repositories / Settings: Abbenay AI defaults + LLM providers.
 * Gated by `ansible.settings.view` for the `apme` capability.
 */
export const ApmeAbbenaySettingsTab = () => (
  <RequirePermission
    permission={ansibleSettingsViewPermission}
    resourceRef="apme"
  >
    <ApmeAbbenaySettingsTabContent />
  </RequirePermission>
);
