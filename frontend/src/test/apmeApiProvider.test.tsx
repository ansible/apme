import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  ApmeApiProvider,
  apmeSseUrl,
  apmeWsUrl,
  createDefaultApmeApiAdapter,
  getApmeApiAdapter,
  setApmeApiAdapter,
  useApmeApi,
} from '../api/apmeApiAdapter';
import { useProjectOperationActions } from '@apme/ui-workflow';

function Probe({ label }: { label: string }) {
  const api = useApmeApi();
  return <span>{`${label}:${api.apiBase}`}</span>;
}

describe('ApmeApiProvider isolation (#447)', () => {
  afterEach(() => {
    setApmeApiAdapter(createDefaultApmeApiAdapter());
  });

  it('provides the adapter via context without touching the module singleton', () => {
    const { unmount } = render(
      <ApmeApiProvider adapter={{ apiBase: '/inner/v1' }}>
        <Probe label="inner" />
      </ApmeApiProvider>,
    );
    expect(screen.getByText('inner:/inner/v1')).toBeInTheDocument();
    // Module default is untouched by mount …
    expect(getApmeApiAdapter().apiBase).toBe('/api/v1');
    unmount();
    // … and untouched by unmount (no reset-to-default clobber).
    expect(getApmeApiAdapter().apiBase).toBe('/api/v1');
  });

  it('keeps nested providers isolated from each other', () => {
    const { unmount } = render(
      <ApmeApiProvider adapter={{ apiBase: '/outer/v1' }}>
        <Probe label="outer" />
        <ApmeApiProvider adapter={{ apiBase: '/inner/v1' }}>
          <Probe label="inner" />
        </ApmeApiProvider>
      </ApmeApiProvider>,
    );
    expect(screen.getByText('outer:/outer/v1')).toBeInTheDocument();
    expect(screen.getByText('inner:/inner/v1')).toBeInTheDocument();
    unmount();
    expect(getApmeApiAdapter().apiBase).toBe('/api/v1');
  });

  it('survives unmounting only the inner provider (outer keeps its value)', () => {
    const { rerender } = render(
      <ApmeApiProvider adapter={{ apiBase: '/outer/v1' }}>
        <Probe label="outer" />
        <ApmeApiProvider adapter={{ apiBase: '/inner/v1' }}>
          <Probe label="inner" />
        </ApmeApiProvider>
      </ApmeApiProvider>,
    );
    expect(screen.getByText('inner:/inner/v1')).toBeInTheDocument();
    rerender(
      <ApmeApiProvider adapter={{ apiBase: '/outer/v1' }}>
        <Probe label="outer" />
      </ApmeApiProvider>,
    );
    expect(screen.queryByText('inner:/inner/v1')).not.toBeInTheDocument();
    expect(screen.getByText('outer:/outer/v1')).toBeInTheDocument();
    expect(getApmeApiAdapter().apiBase).toBe('/api/v1');
  });

  it('routes migrated hooks through the nearest provider adapter', async () => {
    const fetchStub = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ operation_id: 'op-1' }),
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApmeApiProvider
        adapter={{
          apiBase: '/inner/v1',
          fetch: fetchStub,
          origin: 'https://inner.example',
        }}
      >
        {children}
      </ApmeApiProvider>
    );
    const { result } = renderHook(() => useProjectOperationActions('proj-1'), {
      wrapper,
    });
    await act(async () => {
      await result.current.start('check');
    });
    // Inner apiBase wins and the inner fetch impl is used — the module
    // singleton is never consulted.
    expect(fetchStub).toHaveBeenCalledWith(
      '/inner/v1/projects/proj-1/operation',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(getApmeApiAdapter().apiBase).toBe('/api/v1');
  });

  it('threads explicit adapters through WS/SSE helpers', () => {
    const inner = createDefaultApmeApiAdapter({
      apiBase: '/inner/v1',
      origin: 'https://inner.example',
    });
    expect(apmeWsUrl('/ws/session', inner)).toBe(
      'wss://inner.example/inner/v1/ws/session',
    );
    expect(apmeSseUrl('/operation/events', inner)).toBe(
      'https://inner.example/inner/v1/operation/events',
    );
  });
});
