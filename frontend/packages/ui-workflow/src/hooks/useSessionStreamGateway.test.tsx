import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  ApmeApiProvider,
  apmeWsUrl,
  createDefaultApmeApiAdapter,
  setApmeApiAdapter,
} from '../api/apmeApiAdapter';
import { useSessionStream, getPersistedSession } from './useSessionStream';

/** Minimal WebSocket stand-in capturing instances and close calls. */
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.OPEN;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn((_code?: number) => {
    this.readyState = MockWebSocket.CLOSED;
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
}

function lastSocket(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
  if (!ws) throw new Error('expected a WebSocket to have been created');
  return ws;
}

/** Mutable provider props so rerender() can switch gateway identity. */
let providerProps: { apiBase: string; origin?: string };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApmeApiProvider adapter={providerProps}>{children}</ApmeApiProvider>
  );
}

function sendMsg(ws: MockWebSocket, msg: unknown): void {
  act(() => {
    ws.onmessage?.({ data: JSON.stringify(msg) });
  });
}

describe('useSessionStream gateway invalidation (#447)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
    setApmeApiAdapter(createDefaultApmeApiAdapter());
  });

  it('closes the stale socket but keeps resume material on endpoint change', async () => {
    providerProps = { apiBase: '/api/v1' };
    const { result, rerender, unmount } = renderHook(() => useSessionStream(), {
      wrapper,
    });
    try {
      await act(async () => {
        await result.current.startSession([], {});
      });
      const ws = lastSocket();
      act(() => {
        ws.onopen?.(new Event('open'));
      });
      sendMsg(ws, {
        type: 'session_created',
        session_id: 'sess-A',
        scan_id: 'scan-A',
      });
      expect(result.current.status).toBe('checking');
      expect(getPersistedSession()?.sessionId).toBe('sess-A');

      providerProps = {
        apiBase: 'https://gw-b.example/api/v1',
        origin: 'https://gw-b.example',
      };
      act(() => {
        rerender();
      });

      // Stale socket torn down, resume material kept for the new host.
      expect(ws.close).toHaveBeenCalled();
      expect(getPersistedSession()?.sessionId).toBe('sess-A');
      expect(result.current.status).toBe('disconnected');
      expect(result.current.error).toBe(
        'Gateway changed — reconnect to continue your session.',
      );
      expect(result.current.canReconnect).toBe(true);
    } finally {
      unmount();
    }
  });

  it('leaves an idle hook untouched on endpoint change', () => {
    providerProps = { apiBase: '/api/v1' };
    const { result, rerender, unmount } = renderHook(() => useSessionStream(), {
      wrapper,
    });
    try {
      expect(result.current.status).toBe('idle');

      providerProps = {
        apiBase: 'https://gw-b.example/api/v1',
        origin: 'https://gw-b.example',
      };
      act(() => {
        rerender();
      });

      expect(MockWebSocket.instances).toHaveLength(0);
      expect(result.current.status).toBe('idle');
      expect(result.current.error).toBeNull();
      expect(result.current.canReconnect).toBe(false);
    } finally {
      unmount();
    }
  });

  it('dials the legacy absolute path for the default base (#448 path migration)', async () => {
    const adapter = createDefaultApmeApiAdapter();
    // '/ws/session' on the default /api/v1 base must reproduce the
    // historical absolute path so existing gateways keep working.
    expect(apmeWsUrl('/ws/session', adapter)).toBe(
      apmeWsUrl('/api/v1/ws/session', adapter),
    );

    providerProps = { apiBase: '/api/v1' };
    const { result, unmount } = renderHook(() => useSessionStream(), {
      wrapper,
    });
    try {
      await act(async () => {
        await result.current.startSession([], {});
      });
      expect(lastSocket().url).toBe(
        apmeWsUrl('/api/v1/ws/session', adapter),
      );
    } finally {
      unmount();
    }
  });
});
