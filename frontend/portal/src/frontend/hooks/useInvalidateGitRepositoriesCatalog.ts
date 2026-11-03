import { useCallback } from "react";
import { useApiHolder } from "@backstage/core-plugin-api";
import { gitRepositoriesCatalogApiRef } from "@ansible/backstage-rhaap-common/gitRepositoriesCatalog";

/** Invalidate self-service Git Repositories catalog cache when host is loaded. */
export function useInvalidateGitRepositoriesCatalog(): () => void {
  const holder = useApiHolder();
  return useCallback(() => {
    holder.get(gitRepositoriesCatalogApiRef)?.invalidateCatalogCache();
  }, [holder]);
}
