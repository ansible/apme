import { useEffect, useState } from 'react';
import {
  Checkbox,
  ExpandableSection,
  Flex,
  FlexItem,
  FormSelect,
  FormSelectOption,
  TextInput,
} from '@patternfly/react-core';
import { listAiModels, type AiModelInfo } from '../api/listAiModels';
import { AI_MODEL_STORAGE_KEY } from '../shared/constants';

export interface CheckOptionsFormProps {
  ansibleVersion: string;
  onAnsibleVersionChange: (value: string) => void;
  collections: string;
  onCollectionsChange: (value: string) => void;
  enableAi: boolean;
  onEnableAiChange: (checked: boolean) => void;
  /**
   * When true, remediate uses interactive=false (legacy auto-apply quick-fixes).
   * Default unchecked → interactive Quick-fix review (ADR-062 Option C).
   */
  autoApplyTier1?: boolean;
  onAutoApplyTier1Change?: (checked: boolean) => void;
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
  idPrefix = '',
}: CheckOptionsFormProps) {
  const prefix = idPrefix ? `${idPrefix}-` : '';
  const [models, setModels] = useState<AiModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem(AI_MODEL_STORAGE_KEY) ?? '',
  );

  useEffect(() => {
    listAiModels()
      .then((m) => {
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
      .catch(() => setModels([]));
  }, []);

  const handleModelChange = (value: string) => {
    setSelectedModel(value);
    localStorage.setItem(AI_MODEL_STORAGE_KEY, value);
  };

  return (
    <ExpandableSection toggleText="Advanced Options" style={{ marginTop: 8 }}>
      <Flex direction={{ default: 'column' }} gap={{ default: 'gapMd' }}>
        <FlexItem>
          <label htmlFor={`${prefix}ansible-version`} style={{ display: 'block', marginBottom: 4, fontWeight: 600 }}>
            Ansible Core Version
          </label>
          <TextInput
            id={`${prefix}ansible-version`}
            placeholder="e.g. 2.16"
            value={ansibleVersion}
            onChange={(_e, v) => onAnsibleVersionChange(v)}
          />
        </FlexItem>
        <FlexItem>
          <label htmlFor={`${prefix}collections`} style={{ display: 'block', marginBottom: 4, fontWeight: 600 }}>
            Collections (comma-separated)
          </label>
          <TextInput
            id={`${prefix}collections`}
            placeholder="e.g. ansible.posix, community.general"
            value={collections}
            onChange={(_e, v) => onCollectionsChange(v)}
          />
        </FlexItem>
        <FlexItem>
          <Checkbox
            id={`${prefix}enable-ai`}
            label="Enable AI-assisted remediation (Tier 2)"
            isChecked={enableAi}
            onChange={(_e, checked) => onEnableAiChange(checked)}
          />
        </FlexItem>
        {onAutoApplyTier1Change && (
          <FlexItem>
            <Checkbox
              id={`${prefix}auto-apply-tier1`}
              label="Auto-apply quick-fixes (skip review)"
              description="Applies after you leave Assessment (Remediate). Scan always pauses on findings first. When unchecked (default), Quick-fix proposals pause for per-location Accept / Decline."
              isChecked={autoApplyTier1}
              onChange={(_e, checked) => onAutoApplyTier1Change(checked)}
            />
          </FlexItem>
        )}
        {enableAi && (
          <FlexItem>
            <label htmlFor={`${prefix}ai-model`} style={{ display: 'block', marginBottom: 4, fontWeight: 600 }}>
              AI Model
            </label>
            {models.length === 0 ? (
              <div style={{ opacity: 0.6, fontSize: 13 }}>
                No models available — check Abbenay configuration
              </div>
            ) : (
              <FormSelect
                id={`${prefix}ai-model`}
                value={selectedModel}
                onChange={(_e, v) => handleModelChange(v)}
                aria-label="Select AI model"
              >
                {models.map((m) => (
                  <FormSelectOption
                    key={m.id}
                    value={m.id}
                    label={`${m.name || m.id} (${m.provider})`}
                  />
                ))}
              </FormSelect>
            )}
          </FlexItem>
        )}
      </Flex>
    </ExpandableSection>
  );
}
