/*
 * Injectable Gateway HTTP/WS access for dual-shell UI (standalone + future hosts).
 * Defaults preserve Vite proxy behavior: apiBase=/api/v1, origin=page origin.
 */

import {
  createContext,
  useContext,
  useMemo,
  type ReactNode,
} from 'react';

export type ApmeFetch = typeof fetch;

export interface ApmeApiAdapter {
  /** Fetch implementation (default: global fetch). */
  fetch: ApmeFetch;
  /** REST API prefix including /api/v1 (default: '/api/v1'). */
  apiBase: string;
  /**
   * HTTP(S) origin for absolute WS/SSE URLs.
   * Default: window.location.origin when available.
   */
  origin: string;
}

function defaultOrigin(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return '';
}

export function createDefaultApmeApiAdapter(
  overrides: Partial<ApmeApiAdapter> = {},
): ApmeApiAdapter {
  return {
    // Always resolve globalThis.fetch at call time so test stubs work.
    fetch: ((input: RequestInfo | URL, init?: RequestInit) =>
      globalThis.fetch(input, init)) as ApmeFetch,
    apiBase: '/api/v1',
    origin: defaultOrigin(),
    ...overrides,
  };
}

let currentAdapter: ApmeApiAdapter = createDefaultApmeApiAdapter();

/**
 * Module-level default adapter for non-React callers (plain service
 * modules, tests). React code under an `ApmeApiProvider` must prefer
 * `useApmeApi()` so nested providers stay isolated — the provider never
 * writes here.
 */
export function getApmeApiAdapter(): ApmeApiAdapter {
  return currentAdapter;
}

/** For tests — restore with createDefaultApmeApiAdapter(). */
export function setApmeApiAdapter(adapter: ApmeApiAdapter): void {
  currentAdapter = adapter;
}

/** Strip a single trailing slash so hosts can pass `…/api/v1/` safely. */
function normalizeApiBase(apiBase: string): string {
  return apiBase.length > 1 && apiBase.endsWith('/')
    ? apiBase.slice(0, -1)
    : apiBase;
}

/** Build a REST URL from a path like `/health` or `health`. */
export function apmeApiUrl(path: string, adapter: ApmeApiAdapter = getApmeApiAdapter()): string {
  const apiBase = normalizeApiBase(adapter.apiBase);
  const p = path.startsWith('/') ? path : `/${path}`;
  if (p.startsWith('/api/')) {
    return p;
  }
  return `${apiBase}${p}`;
}

function resolveFullPath(path: string, adapter: ApmeApiAdapter): string {
  const apiBase = normalizeApiBase(adapter.apiBase);
  const p = path.startsWith('/') ? path : `/${path}`;
  if (p.startsWith('/api/')) {
    return p;
  }
  return `${apiBase}${p}`;
}

function isSameOrigin(origin: string): boolean {
  if (!origin) return true;
  if (typeof window === 'undefined' || !window.location?.origin) return false;
  return origin === window.location.origin;
}

/** WebSocket URL for a path like `/api/v1/ws/session` or `/ws/session`. */
export function apmeWsUrl(path: string, adapter: ApmeApiAdapter = getApmeApiAdapter()): string {
  const { origin } = adapter;
  const fullPath = resolveFullPath(path, adapter);
  // Absolute apiBase (e.g. https://gw.example/api/v1) already produced a full URL.
  if (/^https?:\/\//i.test(fullPath)) {
    const url = new URL(fullPath);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }
  // Standalone / Vite proxy: build from the page host (pre-adapter behavior).
  if (isSameOrigin(origin)) {
    const proto =
      typeof window !== 'undefined' && window.location.protocol === 'https:'
        ? 'wss:'
        : 'ws:';
    const host =
      typeof window !== 'undefined' ? window.location.host : 'localhost';
    return `${proto}//${host}${fullPath}`;
  }
  const url = new URL(fullPath, origin.endsWith('/') ? origin : `${origin}/`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

/** EventSource URL for SSE endpoints. */
export function apmeSseUrl(path: string, adapter: ApmeApiAdapter = getApmeApiAdapter()): string {
  const { origin } = adapter;
  const fullPath = resolveFullPath(path, adapter);
  // Relative URL keeps EventSource on the Vite proxy (same as pre-adapter).
  if (isSameOrigin(origin)) {
    return fullPath;
  }
  return new URL(fullPath, origin.endsWith('/') ? origin : `${origin}/`).toString();
}

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
