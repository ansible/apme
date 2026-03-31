import { useCallback, useEffect, useState } from 'react';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  ActionGroup,
  Button,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Label,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  TextInput,
} from '@patternfly/react-core';
import {
  Table,
  Tbody,
  Td,
  Th,
  Thead,
  Tr,
} from '@patternfly/react-table';
import { PencilAltIcon, TrashIcon } from '@patternfly/react-icons';
import {
  listAiModels,
  listGalaxyServers,
  createGalaxyServer,
  updateGalaxyServer,
  deleteGalaxyServer,
} from '../services/api';
import type { AiModelInfo, GalaxyServer } from '../types/api';

export const AI_MODEL_STORAGE_KEY = 'apme-ai-model';

// ── Galaxy Server Form Modal ────────────────────────────────────────

interface GalaxyServerFormState {
  name: string;
  url: string;
  token: string;
  auth_url: string;
}

const EMPTY_FORM: GalaxyServerFormState = { name: '', url: '', token: '', auth_url: '' };

function GalaxyServerFormModal({
  isOpen,
  editing,
  onClose,
  onSaved,
}: {
  isOpen: boolean;
  editing: GalaxyServer | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<GalaxyServerFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setForm(
        editing
          ? { name: editing.name, url: editing.url, token: '', auth_url: editing.auth_url }
          : EMPTY_FORM,
      );
      setError('');
    }
  }, [isOpen, editing]);

  const handleSave = async () => {
    if (!form.name.trim() || !form.url.trim()) {
      setError('Name and URL are required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      if (editing) {
        const body: Record<string, string> = {
          name: form.name,
          url: form.url,
          auth_url: form.auth_url,
          token: form.token,
        };
        await updateGalaxyServer(editing.id, body);
      } else {
        await createGalaxyServer({
          name: form.name,
          url: form.url,
          token: form.token,
          auth_url: form.auth_url,
        });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} variant="medium">
      <ModalHeader title={editing ? 'Edit Galaxy Server' : 'Add Galaxy Server'} />
      <ModalBody>
        {error && (
          <div style={{ color: 'var(--pf-t--global--color--status--danger--default)', marginBottom: 16 }}>
            {error}
          </div>
        )}
        <Form>
          <FormGroup label="Name" isRequired fieldId="gs-name">
            <TextInput
              id="gs-name"
              value={form.name}
              onChange={(_e, v) => setForm((f) => ({ ...f, name: v }))}
              placeholder="automation_hub"
            />
          </FormGroup>
          <FormGroup label="URL" isRequired fieldId="gs-url">
            <TextInput
              id="gs-url"
              value={form.url}
              onChange={(_e, v) => setForm((f) => ({ ...f, url: v }))}
              placeholder="https://console.redhat.com/api/automation-hub/"
            />
          </FormGroup>
          <FormGroup
            label={editing ? 'Token (leave blank to keep current)' : 'Token'}
            fieldId="gs-token"
          >
            <TextInput
              id="gs-token"
              type="password"
              value={form.token}
              onChange={(_e, v) => setForm((f) => ({ ...f, token: v }))}
            />
          </FormGroup>
          <FormGroup label="Auth URL (SSO endpoint)" fieldId="gs-auth-url">
            <TextInput
              id="gs-auth-url"
              value={form.auth_url}
              onChange={(_e, v) => setForm((f) => ({ ...f, auth_url: v }))}
              placeholder="https://sso.redhat.com/auth/realms/..."
            />
          </FormGroup>
        </Form>
      </ModalBody>
      <ModalFooter>
        <Button variant="primary" onClick={handleSave} isLoading={saving} isDisabled={saving}>
          {editing ? 'Save' : 'Add'}
        </Button>
        <Button variant="link" onClick={onClose}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  );
}

// ── Galaxy Servers Section ──────────────────────────────────────────

function GalaxyServersSection() {
  const [servers, setServers] = useState<GalaxyServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<GalaxyServer | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listGalaxyServers()
      .then(setServers)
      .catch(() => setServers([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleAdd = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const handleEdit = (s: GalaxyServer) => {
    setEditing(s);
    setModalOpen(true);
  };

  const handleDelete = async (s: GalaxyServer) => {
    if (!window.confirm(`Delete Galaxy server "${s.name}"?`)) return;
    try {
      await deleteGalaxyServer(s.id);
      refresh();
    } catch {
      /* best effort */
    }
  };

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Galaxy Servers</h3>
        <Button variant="primary" size="sm" onClick={handleAdd}>
          Add server
        </Button>
      </div>

      {loading ? (
        <div style={{ opacity: 0.6 }}>Loading...</div>
      ) : servers.length === 0 ? (
        <div style={{ opacity: 0.6, marginBottom: 16 }}>
          No Galaxy servers configured. Add one to enable authenticated
          collection downloads from Automation Hub or private Galaxy instances.
        </div>
      ) : (
        <Table aria-label="Galaxy servers" variant="compact">
          <Thead>
            <Tr>
              <Th>Name</Th>
              <Th>URL</Th>
              <Th>Token</Th>
              <Th>Auth URL</Th>
              <Th><span className="pf-v6-screen-reader">Actions</span></Th>
            </Tr>
          </Thead>
          <Tbody>
            {servers.map((s) => (
              <Tr key={s.id}>
                <Td dataLabel="Name">{s.name}</Td>
                <Td dataLabel="URL">
                  <span style={{ fontSize: 13, wordBreak: 'break-all' }}>{s.url}</span>
                </Td>
                <Td dataLabel="Token">
                  {s.has_token ? (
                    <Label color="green" isCompact>configured</Label>
                  ) : (
                    <span style={{ opacity: 0.5 }}>none</span>
                  )}
                </Td>
                <Td dataLabel="Auth URL">
                  {s.auth_url ? (
                    <span style={{ fontSize: 13, wordBreak: 'break-all' }}>{s.auth_url}</span>
                  ) : (
                    <span style={{ opacity: 0.5 }}>&mdash;</span>
                  )}
                </Td>
                <Td isActionCell>
                  <ActionGroup>
                    <Button
                      variant="plain"
                      aria-label={`Edit ${s.name}`}
                      onClick={() => handleEdit(s)}
                    >
                      <PencilAltIcon />
                    </Button>
                    <Button
                      variant="plain"
                      aria-label={`Delete ${s.name}`}
                      onClick={() => handleDelete(s)}
                      isDanger
                    >
                      <TrashIcon />
                    </Button>
                  </ActionGroup>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}

      <p style={{ marginTop: 12, opacity: 0.6, fontSize: 13 }}>
        Galaxy servers are injected into every scan and remediate operation.
        Tokens are stored in the Gateway database.
      </p>

      <GalaxyServerFormModal
        isOpen={modalOpen}
        editing={editing}
        onClose={() => setModalOpen(false)}
        onSaved={refresh}
      />
    </>
  );
}

// ── Main Settings Page ──────────────────────────────────────────────

export function SettingsPage() {
  const [models, setModels] = useState<AiModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? '',
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listAiModels()
      .then((m) => {
        setModels(m);
        const stored = localStorage.getItem(AI_MODEL_STORAGE_KEY);
        const ids = new Set(m.map((x) => x.id));
        if (stored && ids.has(stored)) {
          setSelectedModel(stored);
        } else {
          const first = m[0];
          const fallback = first?.id ?? '';
          setSelectedModel(fallback);
          if (fallback) {
            localStorage.setItem(AI_MODEL_STORAGE_KEY, fallback);
          } else {
            localStorage.removeItem(AI_MODEL_STORAGE_KEY);
          }
        }
      })
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  }, []);

  const handleChange = useCallback((value: string) => {
    setSelectedModel(value);
    if (value) {
      localStorage.setItem(AI_MODEL_STORAGE_KEY, value);
    } else {
      localStorage.removeItem(AI_MODEL_STORAGE_KEY);
    }
  }, []);

  const current = models.find((m) => m.id === selectedModel);

  return (
    <PageLayout>
      <PageHeader title="Settings" />

      <div style={{ padding: '0 24px 24px', maxWidth: 800 }}>
        <h3 style={{ marginBottom: 16 }}>AI Configuration</h3>

        <FormGroup label="Default AI model" fieldId="ai-model">
          {loading ? (
            <div style={{ opacity: 0.6 }}>Loading models...</div>
          ) : models.length === 0 ? (
            <div style={{ opacity: 0.6 }}>
              No models available. Ensure the Abbenay AI service is running and
              configured with at least one model.
            </div>
          ) : (
            <>
              <FormSelect
                id="ai-model"
                value={selectedModel}
                onChange={(_e, v) => handleChange(v)}
                aria-label="Select AI model"
              >
                {models.map((m) => (
                  <FormSelectOption
                    key={m.id}
                    value={m.id}
                    label={`${m.id} (${m.provider})`}
                  />
                ))}
              </FormSelect>

              {current && (
                <div style={{ marginTop: 8 }}>
                  <Label color="blue" isCompact>
                    {current.provider}
                  </Label>{' '}
                  <span style={{ opacity: 0.7, fontSize: 13 }}>
                    {current.name}
                  </span>
                </div>
              )}
            </>
          )}
        </FormGroup>

        <p style={{ marginTop: 24, opacity: 0.6, fontSize: 13, marginBottom: 32 }}>
          The selected model is used for AI-assisted remediation (Tier 2) when
          starting a new scan with AI enabled. This preference is stored in your
          browser.
        </p>

        <hr style={{ border: 'none', borderTop: '1px solid var(--pf-t--global--border--color--default)', marginBottom: 24 }} />

        <GalaxyServersSection />
      </div>
    </PageLayout>
  );
}
