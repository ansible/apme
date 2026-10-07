import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApmeApiProvider } from '../api/apmeApiAdapter';
import { AI_MODEL_STORAGE_KEY, SettingsPage } from '../pages/SettingsPage';

function jsonResponse(data: unknown): Response {
  return {
    ok: true,
    json: async () => data,
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function apiFetch(modelResponse: Promise<Response>): typeof fetch {
  return vi.fn((input: RequestInfo | URL) => {
    if (String(input).endsWith('/ai/models')) return modelResponse;
    return Promise.resolve(jsonResponse([]));
  }) as typeof fetch;
}

function renderSettings(fetch: typeof globalThis.fetch) {
  return (
    <ApmeApiProvider adapter={{ fetch }}>
      <SettingsPage />
    </ApmeApiProvider>
  );
}

describe('SettingsPage model loading', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('ignores a successful model response from a replaced adapter', async () => {
    const oldResponse = deferred<Response>();
    const oldFetch = apiFetch(oldResponse.promise);
    const newFetch = apiFetch(
      Promise.resolve(
        jsonResponse([
          { id: 'new-model', provider: 'new-provider', name: 'New model' },
        ]),
      ),
    );
    const view = render(renderSettings(oldFetch));

    view.rerender(renderSettings(newFetch));

    const select = await screen.findByLabelText('Select AI model');
    expect(select).toHaveValue('new-model');
    expect(localStorage.getItem(AI_MODEL_STORAGE_KEY)).toBe('new-model');

    await act(async () => {
      oldResponse.resolve(
        jsonResponse([
          { id: 'old-model', provider: 'old-provider', name: 'Old model' },
        ]),
      );
      await oldResponse.promise;
    });

    expect(select).toHaveValue('new-model');
    expect(
      screen.getByRole('option', { name: 'new-model (new-provider)' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'old-model (old-provider)' }),
    ).not.toBeInTheDocument();
    expect(localStorage.getItem(AI_MODEL_STORAGE_KEY)).toBe('new-model');
  });

  it('keeps loading while a replaced adapter request fails', async () => {
    const oldResponse = deferred<Response>();
    const newResponse = deferred<Response>();
    const view = render(renderSettings(apiFetch(oldResponse.promise)));

    view.rerender(renderSettings(apiFetch(newResponse.promise)));

    await act(async () => {
      oldResponse.reject(new Error('old gateway failed'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText('Loading models...')).toBeInTheDocument();

    await act(async () => {
      newResponse.resolve(
        jsonResponse([
          { id: 'current-model', provider: 'current-provider', name: 'Current model' },
        ]),
      );
      await newResponse.promise;
    });

    expect(await screen.findByLabelText('Select AI model')).toHaveValue(
      'current-model',
    );
  });
});
