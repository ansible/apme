/** React-scoped adapters; transport lives in the dependency-free client. */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { createDefaultApmeApiAdapter, getApmeApiAdapter, type ApmeApiAdapter } from '@apme/client/transport';
export { apmeApiUrl, apmeWsUrl, apmeSseUrl, createDefaultApmeApiAdapter, getApmeApiAdapter, setApmeApiAdapter, type ApmeApiAdapter, type ApmeFetch } from '@apme/client/transport';

const ApmeApiContext = createContext<ApmeApiAdapter | null>(null);

/**
 * Provide the Gateway adapter via React context. The provider never
 * touches the module-level default, so nested providers stay isolated:
 * each subtree sees its nearest provider value, and unmounting an inner
 * provider cannot clobber the outer one.
 */
export function ApmeApiProvider({
  adapter,
  children,
}: {
  adapter?: Partial<ApmeApiAdapter>;
  children: ReactNode;
}) {
  const value = useMemo(
    () => createDefaultApmeApiAdapter(adapter ?? {}),
    [adapter?.fetch, adapter?.apiBase, adapter?.origin],
  );

  return (
    <ApmeApiContext.Provider value={value}>{children}</ApmeApiContext.Provider>
  );
}

export function useApmeApi(): ApmeApiAdapter {
  const ctx = useContext(ApmeApiContext);
  return ctx ?? getApmeApiAdapter();
}
