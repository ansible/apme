/**
 * React context for providing the ApmeApiClient instance.
 *
 * Both the standalone app and Backstage plugin set up their own provider
 * with the appropriate base URL.
 */

import { createContext, useContext } from 'react';
import { ApmeApiClient } from '../api-client/services';

export const ApmeApiContext = createContext<ApmeApiClient | null>(null);

export function useApiClient(): ApmeApiClient {
  const client = useContext(ApmeApiContext);
  if (!client) {
    throw new Error('useApiClient must be used within an ApmeApiContext.Provider');
  }
  return client;
}
