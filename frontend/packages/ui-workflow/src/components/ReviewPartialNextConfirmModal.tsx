import type { ReactNode } from 'react';
import {
  Button,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
} from '@patternfly/react-core';

export interface ReviewPartialNextConfirmModalProps {
  isOpen: boolean;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  onClose: () => void;
}

export function ReviewPartialNextConfirmModal({
  isOpen,
  title,
  children,
  confirmLabel = 'Continue',
  onConfirm,
  onClose,
}: ReviewPartialNextConfirmModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} variant="small">
      <ModalHeader title={title} />
      <ModalBody>{children}</ModalBody>
      <ModalFooter>
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>
        <Button key="confirm" variant="primary" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </ModalFooter>
    </Modal>
  );
}
