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
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

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
          ...(token ? { scm_token: token } : {}),
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
            <TextField
              label="Project name"
              value={name}
              onChange={setName}
              isRequired
              autoFocus
            />
            <TextField
              label="Repository URL"
              value={repoUrl}
              onChange={setRepoUrl}
              isRequired
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
