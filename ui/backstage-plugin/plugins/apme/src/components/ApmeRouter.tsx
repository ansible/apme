/**
 * Top-level router for the APME Backstage plugin pages.
 *
 * Wraps all child routes with the shared ApmeApiContext.Provider
 * so the shared hooks work seamlessly. The actual API client comes
 * from Backstage's useApi(apmeApiRef).
 */

import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useApi } from '@backstage/core-plugin-api';
import { ApmeApiContext } from '@apme/ui-shared';
import { apmeApiRef } from '../api/types';

import { ApmeHomePage } from './ApmeHomePage/ApmeHomePage';
import { ScanListPage } from './ScanListPage/ScanListPage';
import { ScanDetailPage } from './ScanDetailPage/ScanDetailPage';
import { RuleCatalogPage } from './RuleCatalogPage/RuleCatalogPage';
import { RoiMetricsPage } from './RoiMetricsPage/RoiMetricsPage';
import { RemediationQueuePage } from './RemediationQueuePage/RemediationQueuePage';
import { HealthPage } from './HealthPage/HealthPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export const ApmeRouter: React.FC = () => {
  const apiClient = useApi(apmeApiRef);

  return (
    <QueryClientProvider client={queryClient}>
      <ApmeApiContext.Provider value={apiClient as any}>
        <Routes>
          <Route index element={<ApmeHomePage />} />
          <Route path="scans" element={<ScanListPage />} />
          <Route path="scans/:scanId" element={<ScanDetailPage />} />
          <Route path="rules" element={<RuleCatalogPage />} />
          <Route path="metrics" element={<RoiMetricsPage />} />
          <Route path="remediation" element={<RemediationQueuePage />} />
          <Route path="health" element={<HealthPage />} />
        </Routes>
      </ApmeApiContext.Provider>
    </QueryClientProvider>
  );
};
