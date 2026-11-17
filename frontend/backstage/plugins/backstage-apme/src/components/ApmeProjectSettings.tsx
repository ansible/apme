/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeButton as Button } from "./layout";
import "./layout.css";
import { useState, type FormEvent } from "react";
import {
  Alert,
  Checkbox,
  Flex,
  PasswordField,
  Select,
  Text,
  TextField,
} from "@backstage/ui";
import {
  updateProject,
  type ProjectDetail,
  type ProjectSummary,
  type UpdateProjectRequest,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { useMutation } from "./useMutation";
import { DeleteProject } from "./DeleteProject";
import { adapterIdentity } from "./adapterIdentity";

export interface ApmeProjectSettingsProps {
  project: ProjectDetail;
  adapter: ApmeApiAdapter;
  /** Host must authorize editing this particular project; defaults to denied. */
  canManage?: boolean;
  canDelete?: boolean;
  onDeleted?: () => void;
  onSaved?: () => void;
}

export function ApmeProjectSettings(props: ApmeProjectSettingsProps) {
  if (!props.canManage && !props.canDelete)
    return <Text>Project settings are read-only for this account.</Text>;
  return (
    <div className="apme-stack">
      {props.canManage && (
        <ProjectSettingsForm
          key={`settings-${props.project.id}-${adapterIdentity(props.adapter)}`}
          {...props}
        />
      )}
      {props.canDelete && (
        <DeleteProject
          key={`delete-${props.project.id}-${adapterIdentity(props.adapter)}`}
          project={props.project}
          adapter={props.adapter}
          onDeleted={props.onDeleted}
        />
      )}
    </div>
  );
}

function ProjectSettingsForm({
  project,
  adapter,
  canManage = false,
  onSaved,
}: ApmeProjectSettingsProps) {
  const [baseline, setBaseline] = useState<ProjectSummary>(project);
  const [name, setName] = useState(project.name);
  const [url, setUrl] = useState(project.repo_url);
  const [branch, setBranch] = useState(project.branch);
  const [provider, setProvider] = useState(project.scm_provider ?? "");
  const [token, setToken] = useState("");
  const [removeToken, setRemoveToken] = useState(false);
  const [saved, setSaved] = useState(false);
  const mutation = useMutation(adapter);
  const [requirementsToken, setRequirementsToken] = useState("");
  const [removeRequirementsToken, setRemoveRequirementsToken] = useState(false);

  const changes: UpdateProjectRequest = {};
  if (name.trim() !== baseline.name) changes.name = name.trim();
  if (url.trim() !== baseline.repo_url) changes.repo_url = url.trim();
  if (branch.trim() !== baseline.branch) changes.branch = branch.trim();
  if (provider !== (baseline.scm_provider ?? ""))
    changes.scm_provider = provider || null;
  if (removeToken) changes.scm_token = "";
  else if (token.trim()) changes.scm_token = token.trim();
  if (project.git_requirements_enabled) {
    if (removeRequirementsToken) changes.requirements_token = "";
    else if (requirementsToken.trim())
      changes.requirements_token = requirementsToken.trim();
  }
  const dirty = Object.keys(changes).length > 0;

  function change<T>(setter: (value: T) => void, value: T) {
    setSaved(false);
    setter(value);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canManage || !dirty || !name.trim() || !url.trim() || !branch.trim())
      return;
    let updated: ProjectSummary | undefined;
    await mutation.run(
      async () => {
        updated = await updateProject(project.id, changes, adapter);
      },
      () => {
        if (updated) {
          setBaseline(updated);
          setName(updated.name);
          setUrl(updated.repo_url);
          setBranch(updated.branch);
          setProvider(updated.scm_provider ?? "");
        }
        setRequirementsToken("");
        setRemoveRequirementsToken(false);
        setToken("");
        setRemoveToken(false);
        setSaved(true);
        onSaved?.();
      },
    );
  }

  return (
    <form
      className="apme-ui apme-form apme-panel"
      aria-label="Project settings"
      onSubmit={(event) => void submit(event)}
    >
      <Flex direction="column" gap="4">
        <Text as="h2" variant="title-small">
          Project settings
        </Text>
        <Text>Repository and access settings for this project.</Text>
        {mutation.error && (
          <Alert
            status="danger"
            title="Could not save project"
            description={mutation.error}
          />
        )}
        {saved && <Alert status="success" title="Project settings saved" />}
        <TextField
          label="Project name"
          value={name}
          onChange={(value) => change(setName, value)}
          isRequired
          isDisabled={mutation.saving}
        />
        <TextField
          label="Repository URL"
          value={url}
          onChange={(value) => change(setUrl, value)}
          isRequired
          isDisabled={mutation.saving}
        />
        <TextField
          label="Branch"
          value={branch}
          onChange={(value) => change(setBranch, value)}
          isRequired
          isDisabled={mutation.saving}
        />
        <Select
          selectionMode="single"
          label="SCM provider"
          value={provider}
          options={[
            { id: "", label: "Auto-detect from URL" },
            { id: "github", label: "GitHub" },
            { id: "gitlab", label: "GitLab" },
            { id: "bitbucket", label: "Bitbucket" },
          ]}
          onChange={(value) => {
            if (
              typeof value === "string" &&
              ["", "github", "gitlab", "bitbucket"].includes(value)
            )
              change(setProvider, value);
          }}
          isDisabled={mutation.saving}
          description="Choose a provider for a self-hosted Git server."
        />
        <PasswordField
          label={
            baseline.has_scm_token ? "SCM token (configured)" : "SCM token"
          }
          value={token}
          onChange={(value) => change(setToken, value)}
          isDisabled={mutation.saving || removeToken}
          description="Leave blank to keep the current token. Enter a new value to replace it."
        />
        {baseline.has_scm_token && (
          <Checkbox
            isSelected={removeToken}
            onChange={(value) => {
              change(setRemoveToken, value);
              if (value) setToken("");
            }}
            isDisabled={mutation.saving}
          >
            Remove the stored SCM token
          </Checkbox>
        )}
        {project.git_requirements_enabled && (
          <>
            <PasswordField
              label={
                baseline.has_requirements_token
                  ? "Git requirements credential (configured)"
                  : "Git requirements credential"
              }
              value={requirementsToken}
              onChange={(value) => change(setRequirementsToken, value)}
              isDisabled={mutation.saving || removeRequirementsToken}
              description="Optional override used only for Git collections and roles. Defaults to the project SCM token. Leave blank to keep the saved credential."
            />
            {baseline.has_requirements_token && (
              <Checkbox
                isSelected={removeRequirementsToken}
                onChange={(value) => {
                  change(setRemoveRequirementsToken, value);
                  if (value) setRequirementsToken("");
                }}
                isDisabled={mutation.saving}
              >
                Remove requirements credential and use project SCM token
              </Checkbox>
            )}
          </>
        )}
        <Button
          type="submit"
          isDisabled={
            mutation.saving ||
            !dirty ||
            !name.trim() ||
            !url.trim() ||
            !branch.trim()
          }
        >
          {mutation.saving ? "Saving…" : "Save project"}
        </Button>
      </Flex>
    </form>
  );
}
