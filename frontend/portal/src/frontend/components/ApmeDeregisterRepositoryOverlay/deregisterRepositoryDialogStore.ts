import { Entity, stringifyEntityRef } from "@backstage/catalog-model";

type DeregisterDialogState = {
  open: boolean;
  entity: Entity | null;
  redirectPath: string | null;
};

let state: DeregisterDialogState = {
  open: false,
  entity: null,
  redirectPath: null,
};
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export const deregisterRepositoryDialogStore = {
  getState(): DeregisterDialogState {
    return state;
  },

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  open(entity: Entity, redirectPath: string): void {
    state = { open: true, entity, redirectPath };
    emit();
  },

  close(): void {
    state = { open: false, entity: null, redirectPath: null };
    emit();
  },

  isOpenForEntity(entity: Entity): boolean {
    return (
      state.open &&
      state.entity !== null &&
      stringifyEntityRef(state.entity) === stringifyEntityRef(entity)
    );
  },
};
