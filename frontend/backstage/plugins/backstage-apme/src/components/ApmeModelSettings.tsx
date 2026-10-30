/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Flex, Select, Text } from "@backstage/ui";
import { listAiModels } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { AI_MODEL_STORAGE_KEY } from "@apme/ui-workflow/headless";
import { DataStatus } from "./DataStatus";
import { useLoad } from "./useLoad";

export interface ApmeModelSettingsProps {
  adapter: ApmeApiAdapter;
}

export function ApmeModelSettings({ adapter }: ApmeModelSettingsProps) {
  const load = useCallback(() => listAiModels(adapter), [adapter]);
  const state = useLoad(load);
  const [selected, setSelected] = useState("");
  const [storageError, setStorageError] = useState<string>();
  useEffect(() => {
    if (!state.value) return;
    try {
      const stored = localStorage.getItem(AI_MODEL_STORAGE_KEY);
      const next =
        state.value.find((model) => model.id === stored)?.id ??
        state.value[0]?.id ??
        "";
      setSelected(next);
      if (next) localStorage.setItem(AI_MODEL_STORAGE_KEY, next);
      else localStorage.removeItem(AI_MODEL_STORAGE_KEY);
      setStorageError(undefined);
    } catch {
      setStorageError(
        "Browser storage is unavailable. The model preference could not be saved.",
      );
    }
  }, [state.value]);
  function choose(value: string) {
    try {
      localStorage.setItem(AI_MODEL_STORAGE_KEY, value);
      setSelected(value);
      setStorageError(undefined);
    } catch {
      setStorageError(
        "Browser storage is unavailable. The model preference could not be saved.",
      );
    }
  }
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          AI model preference
        </Text>
        <Button
          variant="secondary"
          onPress={state.reload}
          isDisabled={state.loading}
        >
          Refresh models
        </Button>
      </Flex>
      <DataStatus {...state} subject="AI models" />
      {storageError && (
        <Alert
          status="warning"
          title="Could not save model preference"
          description={storageError}
        />
      )}
      {state.value &&
        (state.value.length ? (
          <Select
            selectionMode="single"
            label="Default AI model"
            value={selected}
            options={state.value.map((model) => ({
              id: model.id,
              label: `${model.name || model.id} (${model.provider})`,
            }))}
            onChange={(value) => {
              if (
                typeof value === "string" &&
                state.value?.some((model) => model.id === value)
              )
                choose(value);
            }}
          />
        ) : (
          <Text>
            No AI models are available. Configure an AI provider to enable AI
            fixes.
          </Text>
        ))}
      <Text>
        The selected model is stored in this browser and used by the existing AI
        remediation workflow.
      </Text>
    </Flex>
  );
}
