/*
 * Copyright Red Hat
 *
 * Resolve/register APME project for a catalog git-repository entity.
 */

import { useEffect, useMemo, useState } from "react";
import {
  discoveryApiRef,
  fetchApiRef,
  useApi,
} from "@backstage/core-plugin-api";
import { useEntity } from "@backstage/plugin-catalog-react";
import type { ApmeApiAdapter } from "@apme/ui-workflow";
import {
  defaultBranchFromEntity,
  normalizeRepoUrlFromEntity,
} from "@ansible/backstage-rhaap-common/catalogEntity";
import { apmeApiRef } from "../api";
import { createApmeUiWorkflowAdapter } from "../api/createApmeUiWorkflowAdapter";
import { registerOrResolveApmeProject } from "../utils/registerOrResolveApmeProject";
import { ensureRepoBranchForScan } from "../utils/ensureRepoBranchForScan";

export type ResolveApmeProjectState = {
  adapter: ApmeApiAdapter | null;
  projectId: string | null;
  error: Error | null;
  unavailable: boolean;
};

/** Shared project resolve used by Quality and Quality activity tabs. */
export function useResolveApmeProject(): ResolveApmeProjectState {
  const { entity } = useEntity();
  const apmeApi = useApi(apmeApiRef);
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);

  const [adapter, setAdapter] = useState<ApmeApiAdapter | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const repoUrl = useMemo(
    () => normalizeRepoUrlFromEntity(entity) ?? undefined,
    [entity],
  );
  const branch = useMemo(() => defaultBranchFromEntity(entity), [entity]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await createApmeUiWorkflowAdapter({
          discoveryApi,
          fetchApi,
        });
        if (!cancelled) setAdapter(next);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e : new Error(String(e)));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [discoveryApi, fetchApi]);

  useEffect(() => {
    let cancelled = false;
    setProjectId(null);
    setError(null);
    setUnavailable(false);
    void (async () => {
      if (!repoUrl) {
        setError(new Error("Entity has no source-location repository URL"));
        return;
      }
      try {
        await ensureRepoBranchForScan(apmeApi, repoUrl, branch);
        const name =
          entity.metadata.name ||
          repoUrl
            .replace(/\/$/, "")
            .split("/")
            .pop()
            ?.replace(/\.git$/, "") ||
          "repository";
        const project = await registerOrResolveApmeProject(apmeApi, {
          name,
          repo_url: repoUrl,
          branch,
        });
        if (!cancelled) setProjectId(project.id);
      } catch (e) {
        if (cancelled) return;
        const err = e instanceof Error ? e : new Error(String(e));
        const msg = err.message.toLowerCase();
        if (
          msg.includes("failed to fetch") ||
          msg.includes("network") ||
          msg.includes("econnrefused") ||
          msg.includes("unavailable")
        ) {
          setUnavailable(true);
        } else {
          setError(err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apmeApi, repoUrl, branch, entity.metadata.name]);

  return { adapter, projectId, error, unavailable };
}
