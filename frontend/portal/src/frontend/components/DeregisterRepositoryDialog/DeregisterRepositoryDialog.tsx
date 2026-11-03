/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useCallback, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  CircularProgress,
} from "@material-ui/core";
import { Alert } from "@material-ui/lab";
import { Entity } from "@backstage/catalog-model";
import { useDeregisterRepository } from "../../hooks/useDeregisterRepository";
import { useInvalidateGitRepositoriesCatalog } from "../../hooks/useInvalidateGitRepositoriesCatalog";

export interface DeregisterRepositoryDialogProps {
  open: boolean;
  entity: Entity;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * Confirmation dialog for deregistering a manually-registered Git repository.
 * Shows loading spinner during the API call and renders an error alert on failure.
 */
export const DeregisterRepositoryDialog = ({
  open,
  entity,
  onClose,
  onConfirm,
}: DeregisterRepositoryDialogProps) => {
  const { deregister, loading } = useDeregisterRepository(entity);
  const invalidateGitRepositoriesCatalog =
    useInvalidateGitRepositoriesCatalog();
  const [error, setError] = useState<string | null>(null);

  const displayName =
    entity.metadata?.title ||
    (entity.spec as { repository_name?: string })?.repository_name ||
    entity.metadata?.name ||
    "this repository";

  const handleDeregister = useCallback(async () => {
    setError(null);
    try {
      await deregister();
      invalidateGitRepositoriesCatalog();
      onConfirm();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  }, [deregister, invalidateGitRepositoriesCatalog, onConfirm]);

  const handleClose = useCallback(() => {
    if (!loading) {
      setError(null);
      onClose();
    }
  }, [loading, onClose]);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      disableBackdropClick
      disableEscapeKeyDown={loading}
      aria-labelledby="deregister-dialog-title"
      aria-describedby="deregister-dialog-description"
    >
      <DialogTitle id="deregister-dialog-title">Remove repository?</DialogTitle>
      <DialogContent>
        <DialogContentText id="deregister-dialog-description">
          Are you sure you want to remove <strong>{displayName}</strong> from
          the catalog?
        </DialogContentText>
        <DialogContentText style={{ marginTop: 16 }}>
          This will remove the repository registration from the portal. The
          underlying Git repository, APME project, and scan history will not be
          affected. Portal-driven tracking stops; independently configured APME
          schedules are unchanged. Work already started may finish. You can
          re-register it later if needed.
        </DialogContentText>
        {error && (
          <Alert severity="error" style={{ marginTop: 16 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={loading}>
          Cancel
        </Button>
        <Button
          onClick={handleDeregister}
          color="secondary"
          variant="contained"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : undefined}
        >
          {loading ? "Removing..." : "Remove"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
