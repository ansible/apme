/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Alert,
  Button,
  Cell,
  Checkbox,
  Column,
  Dialog,
  DialogBody,
  DialogFooter,
  DialogHeader,
  Flex,
  PasswordField,
  Row,
  Select,
  TableBody,
  TableHeader,
  TableRoot,
  Text,
  TextField,
} from "@backstage/ui";
import {
  createGalaxyServer,
  deleteGalaxyServer,
  listGalaxyServers,
  updateGalaxyServer,
  type GalaxyServer,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus, PageControls } from "./DataStatus";
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";

export interface ApmeGalaxySettingsProps {
  adapter: ApmeApiAdapter;
  canView?: boolean;
  canManage?: boolean;
  /** Enable only if the host backend forwards validate_certs on create/update. */
  supportsCertificateSettings?: boolean;
}

export function ApmeGalaxySettings(props: ApmeGalaxySettingsProps) {
  return props.canView ? (
    <GalaxySettingsContent {...props} />
  ) : (
    <Text>Galaxy server settings are unavailable for this account.</Text>
  );
}

function GalaxySettingsContent({
  adapter,
  canManage = false,
  supportsCertificateSettings = false,
}: ApmeGalaxySettingsProps) {
  const load = useCallback(() => listGalaxyServers(adapter), [adapter]);
  const state = useLoad(load);
  const [offset, setOffset] = useState(0);
  const [editingId, setEditingId] = useState<number | "new">();
  const [deletingId, setDeletingId] = useState<number>();
  useEffect(() => {
    setEditingId(undefined);
    setDeletingId(undefined);
    setOffset(0);
  }, [adapter]);
  const editing = state.value?.find((server) => server.id === editingId);
  const deleting = state.value?.find((server) => server.id === deletingId);
  const page = Math.min(
    offset,
    Math.max(0, Math.ceil((state.value?.length ?? 0) / 25) - 1) * 25,
  );
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Galaxy servers
        </Text>
        <Flex gap="2">
          <Button
            variant="secondary"
            onPress={state.reload}
            isDisabled={state.loading}
          >
            Refresh Galaxy servers
          </Button>
          {canManage && (
            <Button
              onPress={() => setEditingId("new")}
              isDisabled={state.loading || !!state.error}
            >
              Add Galaxy server
            </Button>
          )}
        </Flex>
      </Flex>
      <DataStatus {...state} subject="Galaxy servers" />
      {state.value && (
        <>
          <TableRoot aria-label="Galaxy servers">
            <TableHeader>
              <Column isRowHeader>Name</Column>
              <Column>URL</Column>
              <Column>Authentication URL</Column>
              <Column>Token</Column>
              <Column>Certificates</Column>
              <Column>Actions</Column>
            </TableHeader>
            <TableBody
              items={state.value.slice(page, page + 25)}
              renderEmptyState={() => "No Galaxy servers configured."}
            >
              {(server) => (
                <Row id={server.id}>
                  <Cell>{server.name}</Cell>
                  <Cell>{server.url}</Cell>
                  <Cell>{server.auth_url || "None"}</Cell>
                  <Cell>{server.has_token ? "Configured" : "None"}</Cell>
                  <Cell>
                    {server.validate_certs == null
                      ? "Deployment default"
                      : server.validate_certs
                        ? "Verify"
                        : "Skip verification"}
                  </Cell>
                  <Cell>
                    {canManage ? (
                      <Flex gap="2">
                        <Button
                          variant="secondary"
                          onPress={() => setEditingId(server.id)}
                        >
                          Edit {server.name}
                        </Button>
                        <Button
                          variant="secondary"
                          onPress={() => setDeletingId(server.id)}
                        >
                          Delete {server.name}
                        </Button>
                      </Flex>
                    ) : (
                      "Read only"
                    )}
                  </Cell>
                </Row>
              )}
            </TableBody>
          </TableRoot>
          <PageControls
            offset={page}
            size={25}
            hasNext={page + 25 < state.value.length}
            busy={false}
            onChange={setOffset}
            subject="Galaxy servers"
          />
          {canManage && (editingId === "new" || editing) && (
            <GalaxyServerForm
              key={editing?.id ?? "new"}
              adapter={adapter}
              editing={editing}
              supportsCertificateSettings={supportsCertificateSettings}
              onClose={() => setEditingId(undefined)}
              onSaved={() => {
                setEditingId(undefined);
                state.reload();
              }}
            />
          )}
          {canManage && deleting && (
            <DeleteGalaxyServer
              key={deleting.id}
              adapter={adapter}
              server={deleting}
              onClose={() => setDeletingId(undefined)}
              onSaved={() => {
                setDeletingId(undefined);
                state.reload();
              }}
            />
          )}
        </>
      )}
    </Flex>
  );
}

function GalaxyServerForm({
  adapter,
  editing,
  supportsCertificateSettings,
  onClose,
  onSaved,
}: {
  adapter: ApmeApiAdapter;
  editing?: GalaxyServer;
  supportsCertificateSettings: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  const [url, setUrl] = useState(editing?.url ?? "");
  const [authUrl, setAuthUrl] = useState(editing?.auth_url ?? "");
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [certificates, setCertificates] = useState(
    editing?.validate_certs == null
      ? "default"
      : String(editing.validate_certs),
  );
  const mutation = useMutation(adapter);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !url.trim()) return;
    const body = {
      name: name.trim(),
      url: url.trim(),
      auth_url: authUrl.trim(),
      ...(clearToken ? { token: "" } : token ? { token } : {}),
      ...(supportsCertificateSettings
        ? {
            validate_certs:
              certificates === "default" ? null : certificates === "true",
          }
        : {}),
    };
    await mutation.run(
      () =>
        editing
          ? updateGalaxyServer(editing.id, body, adapter)
          : createGalaxyServer(body, adapter),
      () => {
        setToken("");
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
        <DialogHeader>
          {editing ? `Edit ${editing.name}` : "Add Galaxy server"}
        </DialogHeader>
        <DialogBody>
          <Flex direction="column" gap="4">
            {mutation.error && (
              <Alert
                status="danger"
                title="Could not save Galaxy server"
                description={mutation.error}
              />
            )}
            <TextField
              label="Name"
              value={name}
              onChange={setName}
              isRequired
              autoFocus
              isDisabled={mutation.saving}
            />
            <TextField
              label="URL"
              value={url}
              onChange={setUrl}
              isRequired
              isDisabled={mutation.saving}
            />
            <TextField
              label="Authentication URL"
              value={authUrl}
              onChange={setAuthUrl}
              isDisabled={mutation.saving}
            />
            <PasswordField
              label={
                editing
                  ? "Token (leave blank to keep current)"
                  : "Token (optional)"
              }
              value={token}
              onChange={setToken}
              isDisabled={clearToken || mutation.saving}
            />
            {editing && (
              <Checkbox
                isSelected={clearToken}
                onChange={(value) => {
                  setClearToken(value);
                  if (value) setToken("");
                }}
                isDisabled={mutation.saving}
              >
                Remove saved token
              </Checkbox>
            )}
            {supportsCertificateSettings ? (
              <Select
                selectionMode="single"
                label="TLS certificate verification"
                value={certificates}
                options={[
                  { id: "default", label: "Use deployment default" },
                  { id: "true", label: "Verify certificates" },
                  { id: "false", label: "Skip verification" },
                ]}
                onChange={(value) => setCertificates(String(value))}
                isDisabled={mutation.saving}
              />
            ) : (
              <Text>
                Certificate verification is managed by the deployment.
              </Text>
            )}
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
            isDisabled={mutation.saving || !name.trim() || !url.trim()}
          >
            {mutation.saving ? "Saving…" : "Save Galaxy server"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}

function DeleteGalaxyServer({
  adapter,
  server,
  onClose,
  onSaved,
}: {
  adapter: ApmeApiAdapter;
  server: GalaxyServer;
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
      <DialogHeader>{`Delete ${server.name}?`}</DialogHeader>
      <DialogBody>
        <Flex direction="column" gap="4">
          <Text>
            Future checks will stop using this Galaxy server and its saved
            credentials.
          </Text>
          {mutation.error && (
            <Alert
              status="danger"
              title="Could not delete Galaxy server"
              description={mutation.error}
            />
          )}
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
          isDisabled={mutation.saving}
          onPress={() =>
            void mutation.run(
              () => deleteGalaxyServer(server.id, adapter),
              onSaved,
            )
          }
        >
          {mutation.saving ? "Deleting…" : "Delete Galaxy server"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
