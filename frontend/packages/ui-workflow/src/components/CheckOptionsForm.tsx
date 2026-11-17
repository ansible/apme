import { useEffect, useState } from 'react';
import {
  Accordion,
  AccordionTrigger,
  AccordionPanel,
  Checkbox,
  Flex,
  Select,
  TextField,
} from '@backstage/ui';
import { listAiModels, type AiModelInfo } from '../api/listAiModels';
import { useApmeApi } from '../api/apmeApiAdapter';
import { AI_MODEL_STORAGE_KEY } from '../shared/constants';
import { ANSIBLE_CORE_VERSION_OPTIONS } from '../shared/ansibleCoreVersions';

export interface CheckOptionsFormProps {
  ansibleVersion: string;
  onAnsibleVersionChange: (value: string) => void;
  collections: string;
  onCollectionsChange: (value: string) => void;
  enableAi: boolean;
  onEnableAiChange: (checked: boolean) => void;
  /**
   * When true, remediate uses interactive=false (legacy auto-apply rule-based fixes).
   * Default unchecked → interactive rule-based fix review (ADR-062 Option C).
   */
  autoApplyTier1?: boolean;
  onAutoApplyTier1Change?: (checked: boolean) => void;
  /** Platform-level AI availability flag; hides AI controls when false and defaults to true for standalone SPA backward compatibility. */
  showAiOptions?: boolean;
  /** Hide the Collections text input from the form. Defaults to true for standalone SPA backward compatibility. */
  showCollections?: boolean;
  idPrefix?: string;
}

export function CheckOptionsForm({
  ansibleVersion,
  onAnsibleVersionChange,
  collections,
  onCollectionsChange,
  enableAi,
  onEnableAiChange,
  autoApplyTier1 = false,
  onAutoApplyTier1Change,
  showAiOptions = true,
  showCollections = true,
  idPrefix = '',
}: CheckOptionsFormProps) {
  const prefix = idPrefix ? `${idPrefix}-` : '';
  const api = useApmeApi();
  const [models, setModels] = useState<AiModelInfo[]>([]);
  const [customVersionSelected, setCustomVersionSelected] = useState(false);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? '',
  );

  useEffect(() => {
    if (
      ANSIBLE_CORE_VERSION_OPTIONS.some((version) => version === ansibleVersion)
    ) {
      setCustomVersionSelected(false);
    } else if (ansibleVersion) {
      setCustomVersionSelected(true);
    }
  }, [ansibleVersion]);

  useEffect(() => {
    let cancelled = false;
    listAiModels(api)
      .then((m) => {
        if (cancelled) return;
        setModels(m);
        const stored = localStorage.getItem(AI_MODEL_STORAGE_KEY);
        if (stored && m.some((x) => x.id === stored)) {
          setSelectedModel(stored);
        } else if (m.length > 0 && m[0]) {
          const fallback = m[0].id;
          setSelectedModel(fallback);
          localStorage.setItem(AI_MODEL_STORAGE_KEY, fallback);
        }
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const handleModelChange = (value: string) => {
    setSelectedModel(value);
    localStorage.setItem(AI_MODEL_STORAGE_KEY, value);
  };

  return (
    <Accordion style={{ marginTop: 8 }}>
      <AccordionTrigger title="Advanced Options" />
      <AccordionPanel>
        <Flex direction="column" gap="4">
          <div>
            <Select
              label="Ansible Core Version"
              id={`${prefix}ansible-version`}
              value={
                customVersionSelected
                  ? '__custom__'
                  : ansibleVersion || '__default__'
              }
              onChange={(key) => {
                const v = String(key);
                if (v === '__custom__') {
                  setCustomVersionSelected(true);
                  if (
                    ANSIBLE_CORE_VERSION_OPTIONS.some(
                      (version) => version === ansibleVersion,
                    )
                  ) {
                    onAnsibleVersionChange('');
                  }
                } else {
                  setCustomVersionSelected(false);
                  onAnsibleVersionChange(v === '__default__' ? '' : v);
                }
              }}
              options={[
                { id: '__default__', label: 'Engine default' },
                { id: '__custom__', label: 'Custom version…' },
                ...ANSIBLE_CORE_VERSION_OPTIONS.map((version) => ({
                  id: version,
                  label: `ansible-core ${version}`,
                })),
              ]}
            />
            {customVersionSelected && (
              <TextField
                aria-label="Custom Ansible Core version"
                placeholder="e.g. 2.16.3 or 2.21"
                value={ansibleVersion}
                onChange={onAnsibleVersionChange}
                style={{ marginTop: 8 }}
              />
            )}
          </div>
          {showCollections && (
            <div>
              <TextField
                label="Collections (comma-separated)"
                id={`${prefix}collections`}
                placeholder="e.g. ansible.posix, community.general"
                value={collections}
                onChange={onCollectionsChange}
              />
            </div>
          )}
          {showAiOptions && (
            <div>
              <Checkbox
                id={`${prefix}enable-ai`}

                isSelected={enableAi}
                onChange={onEnableAiChange}
              >
                Enable AI fix
              </Checkbox>
            </div>
          )}
          {showAiOptions && onAutoApplyTier1Change && (
            <div>
              <Checkbox
                id={`${prefix}auto-apply-tier1`}

                aria-describedby={`${prefix}auto-apply-tier1-description`}
                isSelected={autoApplyTier1}
                onChange={onAutoApplyTier1Change}
              >
                Auto-apply rule-based fixes (skip review)
              </Checkbox>
              <div id={`${prefix}auto-apply-tier1-description`}>
                Applies after you leave Assessment (Remediate). Scan always
                pauses on findings first. When unchecked (default), rule-based
                fix proposals pause for per-location Accept / Decline.
              </div>
            </div>
          )}
          {showAiOptions && enableAi && (
            <div>
              {models.length === 0 ? (
                <div style={{ opacity: 0.6, fontSize: 13 }}>
                  No models available — check Abbenay configuration
                </div>
              ) : (
                <Select
                  label="AI Model"
                  id={`${prefix}ai-model`}
                  value={selectedModel}
                  onChange={(value) => handleModelChange(String(value))}
                  aria-label="Select AI model"
                  options={models.map((m) => ({
                    id: m.id,
                    label: `${m.name || m.id} (${m.provider})`,
                  }))}
                />
              )}
            </div>
          )}
        </Flex>
      </AccordionPanel>
    </Accordion>
  );
}
