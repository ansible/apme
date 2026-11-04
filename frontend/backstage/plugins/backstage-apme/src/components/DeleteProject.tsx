/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeDialogFooter as DialogFooter } from "./layout";
import { useEffect, useState } from "react";
import {
  Alert,
  DialogBody,
  DialogHeader,
  Text,
  TextField,
} from "@backstage/ui";
import {
  apiErrorMessage,
  deleteProject,
  getProject,
  type ProjectDetail,
} from "@apme/client";
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
  const [active, setActive] = useState(!!project.active_operation);
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const [checkError, setCheckError] = useState<string>();
  const mutation = useMutation(adapter);
  useEffect(() => {
    if (!open) return;
    let current = true;
    setChecking(true);
    setChecked(false);
    setCheckError(undefined);
    void getProject(project.id, adapter)
      .then((latest) => {
        if (current) {
          setActive(!!latest.active_operation);
          setChecked(true);
        }
      })
      .catch((error: unknown) => {
        if (current) setCheckError(apiErrorMessage(error));
      })
      .finally(() => {
        if (current) setChecking(false);
      });
    return () => {
      current = false;
    };
  }, [adapter, open, project.id]);
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
          The latest project status reports an active operation. Confirm its
          status in the deletion dialog before deleting.
        </Text>
      )}
      <Button
        variant="secondary"
        destructive
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
              {checking && (
                <Text role="status">Checking project activity…</Text>
              )}
              {checkError && (
                <Alert
                  status="danger"
                  title="Could not verify project activity"
                  description={checkError}
                />
              )}
              {!checking && checked && active && (
                <Alert
                  status="warning"
                  title="Project operation is active"
                  description="Wait for the operation to finish or cancel it before deleting this project."
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
                mutation.saving ||
                checking ||
                !checked ||
                !!checkError ||
                confirmation !== project.name ||
                active
              }
              onPress={() =>
                void mutation.run(
                  async () => {
                    const latest = await getProject(project.id, adapter);
                    const hasActiveOperation = !!latest.active_operation;
                    setActive(hasActiveOperation);
                    if (hasActiveOperation)
                      throw new Error(
                        "An operation became active. Wait for it to finish or cancel it before deleting this project.",
                      );
                    await deleteProject(project.id, adapter);
                  },
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
