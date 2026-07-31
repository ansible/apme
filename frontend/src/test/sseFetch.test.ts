import type { Dispatch, SetStateAction } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { parseSseChunk, readSseStream } from '@apme/ui-workflow';
import {
  applyOperationSseEvent,
  type ProjectOperationState,
} from '../../packages/ui-workflow/src/hooks/useProjectOperationState';

describe('parseSseChunk', () => {
  it('parses event and data frames', () => {
    const { events, rest } = parseSseChunk(
      'event: snapshot\ndata: {"status":"cloning"}\n\n' +
        'event: progress\ndata: {"phase":"clone","message":"Cloning"}\n\n',
    );
    expect(rest).toBe('');
    expect(events).toEqual([
      { event: 'snapshot', data: '{"status":"cloning"}' },
      {
        event: 'progress',
        data: '{"phase":"clone","message":"Cloning"}',
      },
    ]);
  });

  it('keeps incomplete trailing frame in rest', () => {
    const { events, rest } = parseSseChunk(
      'event: snapshot\ndata: {"a":1}\n\nevent: progress\ndata: {"partial',
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.event).toBe('snapshot');
    expect(rest).toContain('partial');
  });

  it('joins multi-line data', () => {
    const { events } = parseSseChunk('event: x\ndata: line1\ndata: line2\n\n');
    expect(events[0]?.data).toBe('line1\nline2');
  });
});

describe('readSseStream', () => {
  it('delivers events from a streamed body', async () => {
    const text =
      'event: snapshot\ndata: {"status":"assessed"}\n\n' +
      'event: progress\ndata: {"phase":"scan","message":"done"}\n\n';
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text.slice(0, 20)));
        controller.enqueue(new TextEncoder().encode(text.slice(20)));
        controller.close();
      },
    });
    const seen: Array<{ event: string; data: string }> = [];
    await readSseStream(stream, (ev) => seen.push(ev));
    expect(seen.map((e) => e.event)).toEqual(['snapshot', 'progress']);
  });

  it('flushes TextDecoder at end-of-stream', async () => {
    // Leading byte of "é" (C3 A9) held by stream:true; without decoder.decode()
    // at EOS the pending byte is dropped (data would be "caf", not "caf�").
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('event: message\ndata: caf'),
        );
        controller.enqueue(new Uint8Array([0xc3]));
        controller.close();
      },
    });
    const seen: Array<{ event: string; data: string }> = [];
    await readSseStream(stream, (ev) => seen.push(ev));
    expect(seen).toEqual([{ event: 'message', data: 'caf\uFFFD' }]);
  });

  it('does not flush a partial frame after abort', async () => {
    const ac = new AbortController();
    // Incomplete frame (no trailing blank line) — would flush on clean end.
    const partial = new TextEncoder().encode(
      'event: snapshot\ndata: {"status":"cloning"}',
    );
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(partial);
        // Leave open until abort cancels the reader.
      },
    });
    const seen: Array<{ event: string; data: string }> = [];
    const reading = readSseStream(stream, (ev) => seen.push(ev), ac.signal);
    // Let the first chunk land in the internal buffer, then abort.
    await new Promise((r) => setTimeout(r, 0));
    ac.abort();
    await reading;
    expect(seen).toEqual([]);
  });
});

describe('applyOperationSseEvent', () => {
  it('applies snapshot and marks connected', () => {
    const setState = vi.fn();
    const setConnected = vi.fn();
    applyOperationSseEvent(setState, setConnected, {
      event: 'snapshot',
      data: JSON.stringify({
        operation_id: 'op-1',
        project_id: 'p-1',
        scan_id: 's-1',
        status: 'cloning',
        scan_type: 'check',
        started_at: '2026-01-01T00:00:00Z',
        progress: [],
      }),
    });
    expect(setConnected).toHaveBeenCalledWith(true);
    expect(setState).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'cloning', operation_id: 'op-1' }),
    );
  });

  it('merges operation_budget_seconds on status_changed', () => {
    let state: ProjectOperationState = {
      operation_id: 'op-1',
      project_id: 'p-1',
      scan_id: 's-1',
      status: 'scanning',
      scan_type: 'remediate',
      started_at: '2026-01-01T00:00:00Z',
      progress: [],
    };
    const setState = vi.fn(
      (updater: SetStateAction<ProjectOperationState | null>) => {
        state =
          typeof updater === 'function'
            ? (updater(state) ?? state)
            : (updater ?? state);
      },
    ) as Dispatch<SetStateAction<ProjectOperationState | null>>;
    const setConnected = vi.fn();
    applyOperationSseEvent(setState, setConnected, {
      event: 'status_changed',
      data: JSON.stringify({
        status: 'scanning',
        previous: 'scanning',
        operation_budget_seconds: 600,
      }),
    });
    expect(state.operation_budget_seconds).toBe(600);
    expect(state.status).toBe('scanning');
  });

  it('merges error_code on status_changed', () => {
    let state: ProjectOperationState = {
      operation_id: 'op-1',
      project_id: 'p-1',
      scan_id: 's-1',
      status: 'scanning',
      scan_type: 'remediate',
      started_at: '2026-01-01T00:00:00Z',
      progress: [],
    };
    const setState = vi.fn(
      (updater: SetStateAction<ProjectOperationState | null>) => {
        state =
          typeof updater === 'function'
            ? (updater(state) ?? state)
            : (updater ?? state);
      },
    ) as Dispatch<SetStateAction<ProjectOperationState | null>>;
    const setConnected = vi.fn();
    applyOperationSseEvent(setState, setConnected, {
      event: 'status_changed',
      data: JSON.stringify({
        status: 'failed',
        previous: 'scanning',
        error: 'Operation stalled without progress',
        error_code: 'operation_stalled',
      }),
    });
    expect(state.status).toBe('failed');
    expect(state.error).toBe('Operation stalled without progress');
    expect(state.error_code).toBe('operation_stalled');
  });
});
