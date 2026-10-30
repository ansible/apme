/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useEffect, useMemo, useRef, useState } from "react";
import { apiErrorMessage } from "@apme/client";

/** Single-flight mutation with visible errors and no callbacks after unmount. */
export function useMutation(scope?: unknown) {
  const mounted = useRef(true);
  const owner = useMemo(() => ({ pending: false }), [scope]);
  const currentOwner = useRef(owner);
  currentOwner.current = owner;
  const [state, setState] = useState<{
    owner: typeof owner;
    saving: boolean;
    error?: string;
  }>({ owner, saving: false });
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function run(action: () => Promise<unknown>, onSaved: () => void) {
    if (owner.pending || !mounted.current) return;
    owner.pending = true;
    setState({ owner, saving: true });
    try {
      await action();
      if (mounted.current && currentOwner.current === owner) onSaved();
    } catch (err) {
      if (mounted.current && currentOwner.current === owner)
        setState({ owner, saving: false, error: apiErrorMessage(err) });
    } finally {
      owner.pending = false;
      if (mounted.current && currentOwner.current === owner)
        setState((previous) =>
          previous.owner === owner ? { ...previous, saving: false } : previous,
        );
    }
  }
  return {
    saving: state.owner === owner && state.saving,
    error: state.owner === owner ? state.error : undefined,
    run,
  };
}
