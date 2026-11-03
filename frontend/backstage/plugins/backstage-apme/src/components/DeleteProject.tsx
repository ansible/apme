/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeDialogFooter as DialogFooter } from "./layout";
import { useState } from "react";
import {
  Alert,
  DialogBody,
  DialogHeader,
  Text,
  TextField,
} from "@backstage/ui";
import { deleteProject, type ProjectDetail } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { ApmeButton as Button, ApmeDialog as Dialog } from "./layout";
import { useMutation } from "./useMutation";

export function DeleteProject({
  project,
  adapter,
  onDeleted,
}: {
  project: ProjectDetail;
  adapter: ApmeApiAdapter;
  onDeleted?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleted, setDeleted] = useState(false);
  const mutation = useMutation(adapter);
  const active = !!project.active_operation;
  if (deleted) return <Alert status="success" title="Project deleted" />;
  return (
    <section
      className="apme-ui apme-panel apme-stack"
      aria-label="Delete project"
    >
      <Text as="h3" variant="title-small">
        Delete project
      </Text>
      <Text>
        Remove this project and its recorded check history from APME. The Git
        repository is preserved.
      </Text>
      {active && (
        <Text>
          Wait for the active operation to finish or cancel it before deleting
          this project.
        </Text>
      )}
      <Button
        variant="secondary"
        destructive
        isDisabled={active}
        onPress={() => {
          setConfirmation("");
          setOpen(true);
        }}
      >
        Delete project
      </Button>
      {open && (
        <Dialog
          isOpen
          onOpenChange={(value) => {
            if (!mutation.saving) setOpen(value);
          }}
        >
          <DialogHeader>Delete {project.name}?</DialogHeader>
          <DialogBody>
            <div className="apme-stack">
              <Alert
                status="warning"
                title="This cannot be undone"
                description="The project and its recorded check history will be permanently deleted from APME. This does not delete the Git repository."
              />
              {mutation.error && (
                <Alert
                  status="danger"
                  title="Could not delete project"
                  description={mutation.error}
                />
              )}
              <TextField
                label={`Type ${project.name} to confirm`}
                value={confirmation}
                onChange={setConfirmation}
                isDisabled={mutation.saving}
                autoComplete="off"
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button
              variant="secondary"
              isDisabled={mutation.saving}
              onPress={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              destructive
              isDisabled={
                mutation.saving || confirmation !== project.name || active
              }
              onPress={() =>
                void mutation.run(
                  () => deleteProject(project.id, adapter),
                  () => {
                    setOpen(false);
                    setDeleted(true);
                    onDeleted?.();
                  },
                )
              }
            >
              {mutation.saving ? "Deleting…" : "Delete permanently"}
            </Button>
          </DialogFooter>
        </Dialog>
      )}
    </section>
  );
}
