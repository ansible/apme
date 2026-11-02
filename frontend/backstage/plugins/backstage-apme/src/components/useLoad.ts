/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useEffect, useState } from "react";
import { apiErrorMessage } from "@apme/client";

/** Ignore results from a previous adapter, project or unmounted page. */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  enabled = true,
) {
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  const [state, setState] = useState<{
    load: typeof load;
    revision: number;
    value?: T;
    error?: string;
    loading: boolean;
  }>({ load, revision, loading: true });
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const controller = new AbortController();
    setState({ load, revision, loading: true });
    Promise.resolve()
      .then(() => (active ? load(controller.signal) : undefined))
      .then(
        (value) => {
          if (active) setState({ load, revision, value, loading: false });
        },
        (error) => {
          if (active)
            setState({
              load,
              revision,
              error: apiErrorMessage(error),
              loading: false,
            });
        },
      );
    return () => {
      active = false;
      controller.abort();
    };
  }, [load, revision, enabled]);
  // A new project/adapter must not render the old project's data for one frame.
  const current =
    enabled && state.load === load && state.revision === revision
      ? state
      : { loading: enabled, value: undefined, error: undefined };
  return { ...current, reload };
}
