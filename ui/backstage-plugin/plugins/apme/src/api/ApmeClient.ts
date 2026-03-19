/**
 * Backstage-aware APME API client.
 *
 * Uses the Backstage discovery API to resolve the gateway base URL
 * and the identity API to forward auth tokens.
 */

import {
  DiscoveryApi,
  IdentityApi,
} from '@backstage/core-plugin-api';
import { ApmeApiClient } from '@apme/ui-shared';

export class BackstageApmeClient extends ApmeApiClient {
  static async create(options: {
    discoveryApi: DiscoveryApi;
    identityApi?: IdentityApi;
  }): Promise<BackstageApmeClient> {
    const baseUrl = await options.discoveryApi.getBaseUrl('apme');

    const getHeaders = async (): Promise<Record<string, string>> => {
      if (!options.identityApi) return {};
      const { token } = await options.identityApi.getCredentials();
      return token ? { Authorization: `Bearer ${token}` } : {};
    };

    // Pre-resolve headers for the synchronous getHeaders callback
    let cachedHeaders: Record<string, string> = {};
    try {
      cachedHeaders = await getHeaders();
    } catch {
      // Identity API may not be available in all contexts
    }

    return new BackstageApmeClient({
      baseUrl,
      getHeaders: () => cachedHeaders,
    });
  }
}
