/*
 * Copyright Red Hat
 */

import { useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { Entity } from "@backstage/catalog-model";

/** Switch to the repository Quality tab (in-place or via self-service route). */
export function useNavigateToRepositoryQualityTab(entity?: Entity) {
  const navigate = useNavigate();
  const [, setSearchParams] = useSearchParams();

  return useCallback(
    (entityName?: string) => {
      const slug = entityName ?? entity?.metadata?.name;
      const query = new URLSearchParams();
      query.set("tab", "quality");
      if (slug && window.location.pathname.includes(`/repositories/${slug}`)) {
        setSearchParams(query, { replace: true });
        return;
      }
      if (slug) {
        navigate(`/self-service/repositories/${slug}?${query.toString()}`);
      }
    },
    [entity?.metadata?.name, navigate, setSearchParams],
  );
}
