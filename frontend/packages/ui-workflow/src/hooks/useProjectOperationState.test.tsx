import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  ApmeApiProvider,
  createDefaultApmeApiAdapter,
  setApmeApiAdapter,
} from '../api/apmeApiAdapter';
import {
  useProjectOperationState,
  type ProjectOperationState,
} from './useProjectOperationState';

function opSnapshot(
  scanId: string,
  status: ProjectOperationState['status'] = 'completed',
): ProjectOperationState {
  return {
    operation_id: 'op-1',
    project_id: 'p1',
    scan_id: scanId,
    status,
    scan_type: 'check',
    started_at: new Date(0).toISOString(),
    progress: [],
  };
}

function okResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
  };
}

/** Mutable provider props so rerender() can switch gateway identity. */
let providerProps: { apiBase: string; fetch: typeof fetch; origin?: string };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApmeApiProvider adapter={providerProps}>{children}</ApmeApiProvider>
  );
}

async function flush() {
  await act(async () => {});
}

describe('useProjectOperationState gateway lifecycle (#447)', () => {
  afterEach(() => {
    vi.useRealTimers();
    setApmeApiAdapter(createDefaultApmeApiAdapter());
  });

  it('clears state and polls the new gateway when apiBase changes', async () => {
    const fetchA = vi
      .fn()
      .mockResolvedValue(okResponse(opSnapshot('s-A'))) as unknown as typeof fetch;
    // Gateway B never answers: the cleared (null) state stays observable.
    const fetchB = vi.fn().mockReturnValue(
      new Promise(() => {}),
    ) as unknown as typeof fetch;

    providerProps = { apiBase: '/api/v1', fetch: fetchA };
    const { result, rerender, unmount } = renderHook(
      () => useProjectOperationState('p1'),
      { wrapper },
    );
    try {
      await flush();
      expect(result.current.state?.scan_id).toBe('s-A');
      expect(fetchA).toHaveBeenCalledWith('/api/v1/projects/p1/operation');

      providerProps = { apiBase: '/b/v1', fetch: fetchB };
      rerender();
      await flush();

      // Gateway swap wipes the old snapshot and polls the new base.
      expect(result.current.state).toBeNull();
      expect(fetchB).toHaveBeenCalledWith('/b/v1/projects/p1/operation');
    } finally {
      unmount();
    }
  });

  it('does not clear or refetch when only the fetch closure changes', async () => {
    const fetchA1 = vi
      .fn()
      .mockResolvedValue(okResponse(opSnapshot('s-A'))) as unknown as typeof fetch;
    const fetchA2 = vi
      .fn()
      .mockResolvedValue(okResponse(opSnapshot('s-A'))) as unknown as typeof fetch;

    providerProps = { apiBase: '/api/v1', fetch: fetchA1 };
    const { result, rerender, unmount } = renderHook(
      () => useProjectOperationState('p1'),
      { wrapper },
    );
    try {
      await flush();
      expect(result.current.state?.scan_id).toBe('s-A');
      expect(fetchA1).toHaveBeenCalledTimes(1);

      // Same apiBase/origin, new fetch identity: no reconnect storm.
      providerProps = { apiBase: '/api/v1', fetch: fetchA2 };
      rerender();
      await flush();

      expect(result.current.state?.scan_id).toBe('s-A');
      expect(fetchA2).not.toHaveBeenCalled();
    } finally {
      unmount();
    }
  });

  it('retries once on the new gateway after a failed gateway-change poll', async () => {
    vi.useFakeTimers();
    const fetchA = vi
      .fn()
      .mockResolvedValue(okResponse(opSnapshot('s-A'))) as unknown as typeof fetch;
    const fetchB = vi
      .fn()
      .mockRejectedValueOnce(new Error('gateway B down'))
      .mockResolvedValue(okResponse(opSnapshot('s-B'))) as unknown as typeof fetch;

    providerProps = { apiBase: '/api/v1', fetch: fetchA };
    const { result, rerender, unmount } = renderHook(
      () => useProjectOperationState('p1'),
      { wrapper },
    );
    try {
      await act(async () => {});
      expect(result.current.state?.scan_id).toBe('s-A');

      providerProps = { apiBase: '/b/v1', fetch: fetchB };
      rerender();
      await act(async () => {});
      // First poll against B failed after a gateway change: state cleared.
      expect(fetchB).toHaveBeenCalledTimes(1);
      expect(result.current.state).toBeNull();

      // The one-shot 1s retry recovers on the new gateway.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      expect(fetchB).toHaveBeenCalledTimes(2);
      expect(fetchB).toHaveBeenLastCalledWith('/b/v1/projects/p1/operation');
      expect(result.current.state?.scan_id).toBe('s-B');
    } finally {
      unmount();
    }
  });

  it('keeps prior state when a same-gateway poll fails', async () => {
    const fetchAMock = vi
      .fn()
      .mockResolvedValue(okResponse(opSnapshot('s-A')));
    const fetchA = fetchAMock as unknown as typeof fetch;

    providerProps = { apiBase: '/api/v1', fetch: fetchA };
    const { result, unmount } = renderHook(
      () => useProjectOperationState('p1'),
      { wrapper },
    );
    try {
      await flush();
      expect(result.current.state?.scan_id).toBe('s-A');

      // Transient failure against the same gateway: no wipe, no null.
      fetchAMock.mockRejectedValueOnce(new Error('blip'));
      await act(async () => {
        await result.current.refresh();
      });
      expect(result.current.state?.scan_id).toBe('s-A');
    } finally {
      unmount();
    }
  });
});
