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
    enabled: boolean;
  }>({ load, revision, loading: true, enabled });
  useEffect(() => {
    if (!enabled) {
      setState({ load, revision, loading: false, enabled: false });
      return;
    }
    let active = true;
    const controller = new AbortController();
    setState((previous) => ({
      load,
      revision,
      value:
        previous.load === load && previous.enabled ? previous.value : undefined,
      loading: true,
      enabled: true,
    }));
    Promise.resolve()
      .then(() => (active ? load(controller.signal) : undefined))
      .then(
        (value) => {
          if (active)
            setState({ load, revision, value, loading: false, enabled: true });
        },
        (error) => {
          if (active)
            setState((previous) => ({
              load,
              revision,
              value:
                previous.load === load && previous.enabled
                  ? previous.value
                  : undefined,
              error: apiErrorMessage(error),
              loading: false,
              enabled: true,
            }));
        },
      );
    return () => {
      active = false;
      controller.abort();
    };
  }, [load, revision, enabled]);
  // A new project/adapter must not render the old project's data for one frame.
  const current =
    enabled &&
    state.load === load &&
    state.enabled &&
    state.revision === revision
      ? state
      : enabled && state.load === load && state.enabled
        ? { loading: true, value: state.value, error: undefined }
        : { loading: enabled, value: undefined, error: undefined };
  return { ...current, reload };
}
