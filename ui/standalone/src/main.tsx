import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApmeApiClient, ApmeApiContext } from '@apme/ui-shared';

import '@patternfly/react-core/dist/styles/base.css';
import './theme/apme-dark.css';

import { App } from './App';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,
      retry: 1,
      staleTime: 5_000,
    },
  },
});

const apiClient = new ApmeApiClient({
  baseUrl: window.location.origin,
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ApmeApiContext.Provider value={apiClient}>
          <App />
        </ApmeApiContext.Provider>
      </QueryClientProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
