import React, { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Content,
  Flex,
  FlexItem,
  Form,
  FormGroup,
  PageSection,
  TextArea,
  TextInput,
} from '@patternfly/react-core';
import { useCreateScan } from '@apme/ui-shared';

export const NewScanPage: React.FC = () => {
  const navigate = useNavigate();
  const createScan = useCreateScan();

  const [projectName, setProjectName] = useState('');
  const [yamlContent, setYamlContent] = useState('');
  const [fileName, setFileName] = useState('playbook.yml');
  const [error, setError] = useState('');

  const handleFileUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      setYamlContent(reader.result as string);
    };
    reader.readAsText(file);
  }, []);

  const handleSubmit = async () => {
    setError('');
    if (!yamlContent.trim()) {
      setError('Paste or upload at least one YAML file to scan.');
      return;
    }
    const name = projectName.trim() || fileName.replace(/\.(yml|yaml)$/, '');
    try {
      const result = await createScan.mutateAsync({
        project_name: name,
        files: { [fileName]: yamlContent },
      });
      navigate(`/scans/${result.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Scan failed');
    }
  };

  return (
    <PageSection>
      <Flex direction={{ default: 'column' }} spaceItems={{ default: 'spaceItemsLg' }}>
        <FlexItem>
          <Content component="h1">New Scan</Content>
          <Content component="p">
            Paste Ansible YAML content or upload a file to scan for policy violations.
          </Content>
        </FlexItem>

        {error && (
          <FlexItem>
            <Alert variant="danger" title="Scan error" isInline>
              {error}
            </Alert>
          </FlexItem>
        )}

        <FlexItem>
          <Card>
            <CardTitle>Scan Configuration</CardTitle>
            <CardBody>
              <Form>
                <FormGroup label="Project name" fieldId="project-name">
                  <TextInput
                    id="project-name"
                    value={projectName}
                    onChange={(_e, v) => setProjectName(v)}
                    placeholder="my-ansible-project"
                  />
                </FormGroup>

                <FormGroup label="File name" fieldId="file-name">
                  <TextInput
                    id="file-name"
                    value={fileName}
                    onChange={(_e, v) => setFileName(v)}
                  />
                </FormGroup>

                <FormGroup label="Upload YAML file" fieldId="file-upload">
                  <input
                    type="file"
                    id="file-upload"
                    accept=".yml,.yaml"
                    onChange={handleFileUpload}
                  />
                </FormGroup>

                <FormGroup label="Or paste YAML content" fieldId="yaml-content">
                  <TextArea
                    id="yaml-content"
                    value={yamlContent}
                    onChange={(_e, v) => setYamlContent(v)}
                    rows={16}
                    className="apme-mono"
                    placeholder="---\n- name: My playbook\n  hosts: all\n  tasks:\n    - command: echo hello"
                  />
                </FormGroup>

                <ActionGroup>
                  <Button
                    variant="primary"
                    onClick={handleSubmit}
                    isLoading={createScan.isPending}
                    isDisabled={createScan.isPending}
                  >
                    Run Scan
                  </Button>
                  <Button variant="link" onClick={() => navigate('/')}>
                    Cancel
                  </Button>
                </ActionGroup>
              </Form>
            </CardBody>
          </Card>
        </FlexItem>
      </Flex>
    </PageSection>
  );
};
