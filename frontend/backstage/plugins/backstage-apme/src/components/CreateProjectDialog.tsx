/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useState, type FormEvent } from "react";
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
} from "@backstage/ui";
import {
  apiErrorMessage,
  createProject,
  type ProjectSummary,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";

export function CreateProjectDialog({
  adapter,
  onCreated,
  onClose,
}: {
  adapter: ApmeApiAdapter;
  onCreated: (project: ProjectSummary) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [token, setToken] = useState("");
  const [provider, setProvider] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  function changeUrl(value: string) {
    setRepoUrl(value);
    if (!nameEdited) {
      setName(
        value
          .trim()
          .replace(/\/+$/, "")
          .replace(/\.git$/, "")
          .split("/")
          .pop()
          ?.replace(/[-_]+/g, " ")
          .replace(/\b\w/g, (letter) => letter.toUpperCase()) ?? "",
      );
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || !name.trim() || !repoUrl.trim() || !branch.trim()) return;
    setSaving(true);
    setError(undefined);
    try {
      const project = await createProject(
        {
          name: name.trim(),
          repo_url: repoUrl.trim(),
          branch: branch.trim(),
          ...(token.trim() ? { scm_token: token.trim() } : {}),
          ...(provider ? { scm_provider: provider } : {}),
        },
        adapter,
      );
      setToken("");
      onCreated(project);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      isOpen
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <form onSubmit={(event) => void submit(event)}>
        <DialogHeader title="Add repository" />
        <DialogBody>
          <Flex direction="column" gap="4">
            {error && <Alert status="danger" title={error} />}
            <Text>
              Add a repository directly for a one-off check or ongoing work. No
              configuration file or catalog metadata is needed in the
              repository.
            </Text>
            <TextField
              label="Repository URL"
              value={repoUrl}
              onChange={changeUrl}
              description="For example, https://github.com/your-org/your-repo.git"
              isRequired
              autoFocus
            />
            <TextField
              label="Project name"
              value={name}
              onChange={(value) => {
                setNameEdited(value.trim().length > 0);
                setName(value);
              }}
              isRequired
              description="Suggested from the repository URL; you can change it."
            />
            <Select
              selectionMode="single"
              label="SCM provider (optional)"
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
                  setProvider(value);
              }}
              description="Choose a provider for a self-hosted Git server."
            />
            <TextField
              label="Branch"
              value={branch}
              onChange={setBranch}
              isRequired
            />
            <PasswordField
              label="SCM token (optional)"
              value={token}
              onChange={setToken}
              description="Sent to APME for private repository access and publishing fixes."
            />
          </Flex>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onPress={onClose} isDisabled={saving}>
            Cancel
          </Button>
          <Button
            type="submit"
            isDisabled={
              saving || !name.trim() || !repoUrl.trim() || !branch.trim()
            }
          >
            {saving ? "Adding…" : "Add repository"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
