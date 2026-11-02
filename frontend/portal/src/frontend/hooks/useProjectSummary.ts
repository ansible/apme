import { useEffect, useState } from "react";
import type { Project } from "../../common/types";
import { projectHasActiveOperation } from "../../common/operationStatus";
import type { ApmeApi } from "../api/ApmeApi";

/** Refresh APME's summary until a background check finishes; the host stays generic. */
export function useProjectSummary(
  api: Pick<ApmeApi, "getProject">,
  projectId: string,
  refreshToken: boolean,
) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const next = await api.getProject(projectId);
        if (cancelled) return;
        setProject(next);
        setError(null);
        if (projectHasActiveOperation(next)) {
          timer = setTimeout(() => void load(), 3000);
        }
      } catch (error_) {
        if (!cancelled) {
          setError(
            error_ instanceof Error ? error_ : new Error(String(error_)),
          );
        }
      }
    }
    setProject(null);
    setError(null);
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, projectId, refreshToken]);

  return { project, error };
}
