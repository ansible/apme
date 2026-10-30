/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogBody,
  DialogFooter,
  DialogHeader,
  Flex,
  PasswordField,
  Select,
  Text,
  TextField,
  TextAreaField,
} from "@backstage/ui";
import {
  deleteAiProvider,
  getAiEngines,
  getAiProviderSettings,
  saveAiProvider,
  type ApmeAiProviderSummary,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";

export interface ApmeAiProvidersProps {
  adapter: ApmeApiAdapter;
  canView?: boolean;
  canManage?: boolean;
  onChanged?: () => void;
}
export function ApmeAiProviders(props: ApmeAiProvidersProps) {
  return props.canView ? <ProvidersContent {...props} /> : null;
}
function ProvidersContent({
  adapter,
  canManage,
  onChanged,
}: ApmeAiProvidersProps) {
  const load = useCallback(() => getAiProviderSettings(adapter), [adapter]);
  const state = useLoad(load);
  const [editingId, setEditingId] = useState<string>();
  const [deletingId, setDeletingId] = useState<string>();
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    setEditingId(undefined);
    setDeletingId(undefined);
    setOffset(0);
  }, [adapter]);
  const selected = state.value?.find((provider) => provider.id === editingId);
  const removing = state.value?.find((provider) => provider.id === deletingId);
  const page = Math.min(
    offset,
    Math.max(0, Math.ceil((state.value?.length ?? 0) / 25) - 1) * 25,
  );
  function saved() {
    setEditingId(undefined);
    setDeletingId(undefined);
    state.reload();
    onChanged?.();
  }
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          AI providers
        </Text>
        <Flex gap="2">
          <Button
            variant="secondary"
            onPress={state.reload}
            isDisabled={state.loading}
          >
            Refresh providers
          </Button>
          {canManage && (
            <Button
              onPress={() => setEditingId("")}
              isDisabled={state.loading || !!state.error}
            >
              Add AI provider
            </Button>
          )}
        </Flex>
      </Flex>
      <DataStatus {...state} subject="AI providers" />
      {state.value && (
        <>
          {state.value.length === 0 && <Text>No AI providers configured.</Text>}
          {state.value.slice(page, page + 25).map((provider) => (
            <section key={provider.id} aria-label={`Provider ${provider.id}`}>
              <Flex direction="column" gap="2">
                <Text as="h4" variant="title-x-small">
                  {provider.id} · {provider.engine}
                </Text>
                <Text>
                  {provider.source === "config"
                    ? "Deployment configuration (read only)"
                    : "Managed provider"}
                </Text>
                <Text>
                  Models: {provider.models.join(", ") || "None configured"}
                </Text>
                {canManage && provider.source !== "config" && (
                  <Flex gap="2">
                    <Button
                      variant="secondary"
                      onPress={() => setEditingId(provider.id)}
                    >
                      Edit {provider.id}
                    </Button>
                    <Button
                      variant="secondary"
                      onPress={() => setDeletingId(provider.id)}
                    >
                      Delete {provider.id}
                    </Button>
                  </Flex>
                )}
              </Flex>
            </section>
          ))}
          <PageControls
            offset={page}
            size={25}
            hasNext={page + 25 < state.value.length}
            busy={false}
            onChange={setOffset}
            subject="AI providers"
          />
          {canManage &&
            (editingId === "" || selected?.source === "managed") && (
              <ProviderForm
                key={editingId}
                adapter={adapter}
                provider={selected}
                onClose={() => setEditingId(undefined)}
                onSaved={saved}
              />
            )}
          {canManage && removing?.source === "managed" && (
            <RemoveProvider
              adapter={adapter}
              provider={removing}
              onClose={() => setDeletingId(undefined)}
              onSaved={saved}
            />
          )}
        </>
      )}
    </Flex>
  );
}
function ProviderForm({
  adapter,
  provider,
  onClose,
  onSaved,
}: {
  adapter: ApmeApiAdapter;
  provider?: ApmeAiProviderSummary;
  onClose: () => void;
  onSaved: () => void;
}) {
  const load = useCallback(() => getAiEngines(adapter), [adapter]);
  const engines = useLoad(load);
  const [id, setId] = useState(provider?.id ?? "");
  const [engine, setEngine] = useState(provider?.engine ?? "");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelText, setModelText] = useState(provider?.models.join("\n") ?? "");
  const mutation = useMutation(adapter);
  const available =
    engines.value?.engines.filter((item) => item.id !== "mock") ?? [];
  const selectedEngine = engine || available[0]?.id || "";
  const needsKey = available.find(
    (item) => item.id === selectedEngine,
  )?.requiresKey;
  const valid =
    /^[A-Za-z0-9_-]+$/.test(id) &&
    !!selectedEngine &&
    (!!provider || !needsKey || !!key.trim());
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    const models = [
      ...new Set(
        modelText
          .split(/\r?\n/)
          .map((value) => value.trim())
          .filter(Boolean),
      ),
    ];
    await mutation.run(
      () =>
        saveAiProvider(
          id,
          {
            engine: selectedEngine,
            secretStore: "file",
            ...(key.trim() ? { apiKey: key.trim() } : {}),
            ...(baseUrl.trim() ? { baseUrl: baseUrl.trim() } : {}),
          },
          models,
          adapter,
        ),
      () => {
        setKey("");
        onSaved();
      },
    );
  }
  return (
    <Dialog
      isOpen
      onOpenChange={(open) => {
        if (!open && !mutation.saving) onClose();
      }}
    >
      <form onSubmit={(event) => void submit(event)}>
        <DialogHeader
          title={provider ? `Edit provider ${provider.id}` : "Add AI provider"}
        />
        <DialogBody>
          <Flex direction="column" gap="4">
            <DataStatus {...engines} subject="AI engines" />
            {mutation.error && (
              <Alert
                status="danger"
                title="Could not save provider"
                description={mutation.error}
              />
            )}
            <TextField
              label="Provider name"
              description="Letters, numbers, underscores and hyphens"
              value={id}
              onChange={setId}
              isRequired
              isDisabled={!!provider || mutation.saving}
            />
            <Select
              selectionMode="single"
              label="Engine"
              value={selectedEngine}
              options={[
                ...new Set([
                  ...available.map((item) => item.id),
                  ...(provider ? [provider.engine] : []),
                ]),
              ].map((value) => ({ id: value, label: value }))}
              onChange={(value) => setEngine(String(value))}
              isDisabled={mutation.saving || engines.loading}
            />
            <PasswordField
              label={provider ? "API key (blank keeps existing)" : "API key"}
              value={key}
              onChange={setKey}
              isDisabled={mutation.saving}
              description="Stored by the AI service. Never saved in browser storage."
            />
            <TextField
              label="Base URL (blank keeps current or engine default)"
              value={baseUrl}
              onChange={setBaseUrl}
              isDisabled={mutation.saving}
            />
            <TextAreaField
              label="Models (one ID per line)"
              value={modelText}
              onChange={setModelText}
              isDisabled={mutation.saving}
            />
          </Flex>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="secondary"
            onPress={onClose}
            isDisabled={mutation.saving}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            isDisabled={
              !valid || mutation.saving || engines.loading || !!engines.error
            }
          >
            {mutation.saving ? "Saving…" : "Save provider"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
function RemoveProvider({
  adapter,
  provider,
  onClose,
  onSaved,
}: {
  adapter: ApmeApiAdapter;
  provider: ApmeAiProviderSummary;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mutation = useMutation(adapter);
  return (
    <Dialog
      isOpen
      onOpenChange={(open) => {
        if (!open && !mutation.saving) onClose();
      }}
    >
      <DialogHeader title={`Delete provider ${provider.id}?`} />
      <DialogBody>
        <Text>
          This provider will no longer be available for AI remediation.
        </Text>
        {mutation.error && (
          <Alert
            status="danger"
            title="Could not delete provider"
            description={mutation.error}
          />
        )}
      </DialogBody>
      <DialogFooter>
        <Button
          variant="secondary"
          onPress={onClose}
          isDisabled={mutation.saving}
        >
          Cancel
        </Button>
        <Button
          isDisabled={mutation.saving}
          onPress={() =>
            void mutation.run(
              () => deleteAiProvider(provider.id, adapter),
              onSaved,
            )
          }
        >
          Delete provider
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
