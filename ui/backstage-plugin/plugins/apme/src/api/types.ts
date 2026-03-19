/**
 * Backstage API ref for the APME plugin.
 *
 * This interface wraps the shared ApmeApiClient and registers it
 * in the Backstage API factory system so each Backstage instance
 * can configure its own gateway URL.
 */

import { createApiRef } from '@backstage/core-plugin-api';
import type { ApmeApiClient } from '@apme/ui-shared';

export const apmeApiRef = createApiRef<ApmeApiClient>({
  id: 'plugin.apme.api',
});
