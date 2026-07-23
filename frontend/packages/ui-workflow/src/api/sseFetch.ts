/**
 * Authenticated SSE over fetch (ReadableStream).
 *
 * Browser EventSource cannot set Authorization headers, so Portal hosts that
 * authenticate via Backstage fetchApi get 401 on /operation/events. This
 * client uses the host adapter's fetch so Bearer (and cookies) work the same
 * as REST — native Vite proxy hosts keep working with plain fetch.
 */

export interface SseEvent {
  event: string;
  data: string;
}

/**
 * Parse SSE frames from a buffer. Returns complete events and unparsed remainder
 * (incomplete trailing frame).
 */
export function parseSseChunk(buffer: string): {
  events: SseEvent[];
  rest: string;
} {
  const events: SseEvent[] = [];
  // SSE frames are separated by a blank line (\n\n or \r\n\r\n).
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop() ?? '';

  for (const part of parts) {
    if (!part.trim()) continue;
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        // Spec: optional single leading space after the colon.
        const raw = line.slice(5);
        dataLines.push(raw.startsWith(' ') ? raw.slice(1) : raw);
      }
      // ignore id:, retry:, comments (:)
    }
    if (dataLines.length === 0) continue;
    events.push({ event, data: dataLines.join('\n') });
  }

  return { events, rest };
}

/**
 * Read an SSE response body, invoking onEvent for each complete frame.
 * Resolves when the stream ends; rejects on read errors (not abort).
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener('abort', onAbort, { once: true });

  try {
    for (;;) {
      if (signal?.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSseChunk(buffer);
      buffer = rest;
      for (const ev of events) {
        onEvent(ev);
      }
    }
    // Flush any final frame that ended without a trailing blank line.
    // Skip on abort — cleanup/unmount must not emit post-cancel events.
    if (!signal?.aborted && buffer.trim()) {
      const { events } = parseSseChunk(`${buffer}\n\n`);
      for (const ev of events) {
        onEvent(ev);
      }
    }
  } finally {
    signal?.removeEventListener('abort', onAbort);
    reader.releaseLock();
  }
}
