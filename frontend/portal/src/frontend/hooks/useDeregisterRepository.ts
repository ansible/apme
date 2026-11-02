/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useCallback, useState } from "react";
import {
  discoveryApiRef,
  fetchApiRef,
  useApi,
} from "@backstage/core-plugin-api";
import { Entity, stringifyEntityRef } from "@backstage/catalog-model";

export interface UseDeregisterRepositoryResult {
  deregister: () => Promise<void>;
  loading: boolean;
  error: Error | null;
}

/**
 * Hook to deregister a manually-registered Git repository from the catalog.
 * Only works for entities with `ansible.io/registration-method: manual`.
 */
export function useDeregisterRepository(
  entity: Entity,
): UseDeregisterRepositoryResult {
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const deregister = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const baseUrl = await discoveryApi.getBaseUrl("portal-operations");
      const url = `${baseUrl}/apme.repository.deregister`;
      const response = await fetchApi.fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          subject: { entityRef: stringifyEntityRef(entity) },
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        let errorMessage: string;
        try {
          const parsed = JSON.parse(errorText);
          if (typeof parsed.error === "string") {
            errorMessage = parsed.error;
          } else if (typeof parsed.error?.message === "string") {
            errorMessage = parsed.error.message;
          } else {
            errorMessage = errorText;
          }
        } catch {
          errorMessage = errorText || response.statusText;
        }
        throw new Error(errorMessage);
      }
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      setError(e);
      throw e;
    } finally {
      setLoading(false);
    }
  }, [discoveryApi, fetchApi, entity]);

  return { deregister, loading, error };
}

/**
 * Check if an entity is a manually-registered Git repository.
 */
export function isManuallyRegisteredRepository(entity: Entity): boolean {
  const registrationMethod =
    entity.metadata?.annotations?.["ansible.io/registration-method"];
  const specType = (entity.spec as { type?: string })?.type;
  return registrationMethod === "manual" && specType === "git-repository";
}
