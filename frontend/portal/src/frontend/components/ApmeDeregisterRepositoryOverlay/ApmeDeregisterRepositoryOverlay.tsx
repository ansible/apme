/*
 * Copyright Red Hat
 *
 * ADR-010: Rendered via getDetailOverlays on the repository detail page,
 * outside the Actions menu, so the confirmation dialog is not torn down
 * when the menu closes.
 */

import { useCallback, useSyncExternalStore } from "react";
import { useNavigate } from "react-router-dom";
import type { GitRepositoryDetailTabContext } from "@ansible/backstage-rhaap-common/gitRepositoriesExtensions";
import { DeregisterRepositoryDialog } from "../DeregisterRepositoryDialog";
import { deregisterRepositoryDialogStore } from "./deregisterRepositoryDialogStore";

export interface ApmeDeregisterRepositoryOverlayProps {
  /**
   * When provided (repository detail page), the dialog only opens when the
   * store entity matches this context entity. When omitted (catalog list),
   * the dialog opens for any entity in the store.
   */
  context?: GitRepositoryDetailTabContext;
}

/**
 * ADR-010 overlay: subscribes to the dialog store and renders
 * DeregisterRepositoryDialog when open.
 * Mounted via getDetailOverlays (detail page) or getCatalogOverlays (catalog list).
 */
export const ApmeDeregisterRepositoryOverlay = ({
  context,
}: ApmeDeregisterRepositoryOverlayProps) => {
  const navigate = useNavigate();

  const storeState = useSyncExternalStore(
    deregisterRepositoryDialogStore.subscribe,
    deregisterRepositoryDialogStore.getState,
    deregisterRepositoryDialogStore.getState,
  );

  const open = context
    ? deregisterRepositoryDialogStore.isOpenForEntity(context.entity)
    : storeState.open;
  const entity = open ? storeState.entity : null;

  const handleClose = useCallback(() => {
    deregisterRepositoryDialogStore.close();
  }, []);

  const handleConfirm = useCallback(() => {
    const { redirectPath } = storeState;
    deregisterRepositoryDialogStore.close();
    if (redirectPath) {
      navigate(redirectPath);
    }
  }, [storeState, navigate]);

  if (!entity) {
    return null;
  }

  return (
    <DeregisterRepositoryDialog
      open={open}
      entity={entity}
      onClose={handleClose}
      onConfirm={handleConfirm}
    />
  );
};
