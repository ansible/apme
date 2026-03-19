/**
 * Hook to get the APME API client from the Backstage API context.
 *
 * Components in the Backstage plugin should use this instead of
 * the shared useApiClient, since the client is registered via
 * Backstage's createApiFactory.
 */

import { useApi } from '@backstage/core-plugin-api';
import { apmeApiRef } from '../api/types';

export function useApmeApi() {
  return useApi(apmeApiRef);
}
