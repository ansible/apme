import { Suspense, type ReactNode } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { PageApp, PageFramework } from '@ansible/ansible-ui-framework';
import { ApmeApiProvider, type ApmeApiAdapter } from '../api/apmeApiAdapter';
import { ApmeMasthead } from './ApmeMasthead';
import { useApmeNavigation } from './useApmeNavigation';
import { useNotificationStream } from './useNotificationStream';

export interface ApmeAppBodyProps {
  /** When false, omit the standalone masthead (host shell provides chrome). */
  showMasthead?: boolean;
  /** Optional masthead override (defaults to ApmeMasthead when showMasthead). */
  masthead?: ReactNode;
}

/**
 * Mountable APME UI body — navigation + routes via PageApp.
 * Hosts supply their own Router (BrowserRouter / MemoryRouter / Backstage).
 */
export function ApmeAppBody({
  showMasthead = true,
  masthead,
}: ApmeAppBodyProps) {
  const navigation = useApmeNavigation();
  useNotificationStream();
  return (
    <PageApp
      masthead={showMasthead ? (masthead ?? <ApmeMasthead />) : undefined}
      navigation={navigation}
      defaultRefreshInterval={30}
    />
  );
}

export interface AppProps {
  /** Override Gateway access (fetch / apiBase / origin). */
  apiAdapter?: Partial<ApmeApiAdapter>;
}

/** Standalone shell: BrowserRouter + API provider + masthead. */
export function App({ apiAdapter }: AppProps = {}) {
  return (
    <BrowserRouter>
      <ApmeApiProvider adapter={apiAdapter}>
        <PageFramework defaultRefreshInterval={30}>
          <Suspense
            fallback={
              <div style={{ padding: 48, textAlign: 'center' }}>Loading...</div>
            }
          >
            <ApmeAppBody />
          </Suspense>
        </PageFramework>
      </ApmeApiProvider>
    </BrowserRouter>
  );
}
