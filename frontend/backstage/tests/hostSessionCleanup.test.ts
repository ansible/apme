// @vitest-environment node
import {
  createDefaultApmeApiAdapter,
  createHostSessionSocket,
} from "@apme/client";

function openBody(status: number, headers?: HeadersInit) {
  const cancel = vi.fn();
  return {
    cancel,
    response: new Response(new ReadableStream({ cancel }), { status, headers }),
  };
}

/** Return an event response that opens a host session and aborts on close. */
function openEventResponse(signal?: AbortSignal): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"event":"open"}\n'));
        signal?.addEventListener(
          "abort",
          () => controller.error(new Error("Aborted")),
          { once: true },
        );
      },
    }),
  );
}

it("cancels a denied creation response without opening an event stream", async () => {
  const denied = openBody(403);
  const fetch = vi.fn(async () => denied.response);
  createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  await vi.waitFor(() => expect(denied.cancel).toHaveBeenCalledOnce());
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("cancels a denied event stream and the cleanup DELETE response", async () => {
  const denied = openBody(403);
  const deleted = openBody(500);
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    if (init?.method === "DELETE") return deleted.response;
    return denied.response;
  });
  createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  await vi.waitFor(() => expect(denied.cancel).toHaveBeenCalledOnce());
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
});

it.each([200, 403])(
  "cancels a message response with status %i",
  async (status) => {
    const message = openBody(status);
    const deleted = openBody(200);
    const fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/messages")) return message.response;
        if (init?.method === "DELETE") return deleted.response;
        if (init?.method === "POST") return new Response('{"id":"channel"}');
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode('{"event":"open"}\n'),
              );
              init?.signal?.addEventListener(
                "abort",
                () => controller.error(new Error("Aborted")),
                { once: true },
              );
            },
          }),
        );
      },
    );
    const socket = createHostSessionSocket(
      createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
      "ws://gateway/session",
    );
    await vi.waitFor(() => expect(socket.readyState).toBe(1));
    if (status === 200) {
      await expect(
        socket.send('{"type":"files_done"}'),
      ).resolves.toBeUndefined();
    } else {
      await expect(socket.send('{"type":"files_done"}')).rejects.toThrow(
        "Upload message rejected by host",
      );
    }
    expect(message.cancel).toHaveBeenCalledOnce();
    if (status === 200) socket.close();
    await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
  },
);

it("retries a contended message and keeps the session open", async () => {
  const contentionCancelled = vi.fn();
  const contention = new Response(
    new ReadableStream({ cancel: contentionCancelled }),
    { status: 429, headers: { "Retry-After": "0" } },
  );
  const deleted = openBody(200);
  let messageRequests = 0;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/messages")) {
      messageRequests++;
      return messageRequests === 1
        ? contention
        : new Response(null, { status: 204 });
    }
    if (init?.method === "DELETE") return deleted.response;
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    return openEventResponse(init?.signal ?? undefined);
  });
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );

  await vi.waitFor(() => expect(socket.readyState).toBe(1));
  await expect(socket.send('{"type":"files_done"}')).resolves.toBeUndefined();

  expect(messageRequests).toBe(2);
  expect(contentionCancelled).toHaveBeenCalledOnce();
  expect(socket.readyState).toBe(1);
  socket.close();
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
});

it("waits for the server Retry-After before retrying a message", async () => {
  let markFirstAttempt!: () => void;
  const firstAttempt = new Promise<void>((resolve) => {
    markFirstAttempt = resolve;
  });
  const deleted = openBody(200);
  let messageRequests = 0;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/messages")) {
      messageRequests++;
      if (messageRequests === 1) {
        markFirstAttempt();
        return new Response(null, {
          status: 429,
          headers: { "Retry-After": "1" },
        });
      }
      return new Response(null, { status: 204 });
    }
    if (init?.method === "DELETE") return deleted.response;
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    return openEventResponse(init?.signal ?? undefined);
  });
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  await vi.waitFor(() => expect(socket.readyState).toBe(1));

  vi.useFakeTimers();
  try {
    const sending = socket.send('{"type":"files_done"}');
    await firstAttempt;
    for (let turn = 0; turn < 10 && vi.getTimerCount() === 0; turn++)
      await Promise.resolve();
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(messageRequests).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(sending).resolves.toBeUndefined();
    expect(messageRequests).toBe(2);
  } finally {
    vi.useRealTimers();
    socket.close();
  }
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
});

it("fails a message after the bounded contention retries are exhausted", async () => {
  const deleted = openBody(200);
  let messageRequests = 0;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/messages")) {
      messageRequests++;
      return new Response("busy", {
        status: 429,
        headers: { "Retry-After": "0" },
      });
    }
    if (init?.method === "DELETE") return deleted.response;
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    return openEventResponse(init?.signal ?? undefined);
  });
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  const onerror = vi.fn();
  socket.onerror = onerror;

  await vi.waitFor(() => expect(socket.readyState).toBe(1));
  await expect(socket.send('{"type":"files_done"}')).rejects.toThrow(
    "Upload message rejected by host",
  );

  expect(messageRequests).toBe(6);
  expect(socket.readyState).toBe(3);
  expect(onerror).toHaveBeenCalledOnce();
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
});

it("fails instead of retrying early for a Retry-After delay beyond its bound", async () => {
  const contention = openBody(429, { "Retry-After": "6" });
  const deleted = openBody(200);
  let messageRequests = 0;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/messages")) {
      messageRequests++;
      return contention.response;
    }
    if (init?.method === "DELETE") return deleted.response;
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    return openEventResponse(init?.signal ?? undefined);
  });
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );

  await vi.waitFor(() => expect(socket.readyState).toBe(1));
  await expect(socket.send('{"type":"files_done"}')).rejects.toThrow(
    "Upload message rejected by host",
  );

  expect(messageRequests).toBe(1);
  expect(contention.cancel).toHaveBeenCalledOnce();
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
});

it("aborts the event reader and deletes the channel on a server close frame", async () => {
  const eventsCancelled = vi.fn();
  const deleted = openBody(200);
  let eventSignal: AbortSignal | undefined;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return new Response('{"id":"channel"}');
    if (init?.method === "DELETE") return deleted.response;
    if (String(input).endsWith("/events")) {
      eventSignal = init?.signal ?? undefined;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode('{"event":"close","code":1000}\n'),
            );
          },
          cancel: eventsCancelled,
        }),
      );
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  const onclose = vi.fn();
  socket.onclose = onclose;

  await vi.waitFor(() => expect(onclose).toHaveBeenCalledOnce());
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
  expect(onclose.mock.calls[0]?.[0].code).toBe(1000);
  expect(eventSignal?.aborted).toBe(true);
  expect(eventsCancelled).toHaveBeenCalledOnce();
});

it("disposes a channel that arrives after the client has already closed", async () => {
  let finish!: (response: Response) => void;
  const created = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const deleted = openBody(200);
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "DELETE" ? deleted.response : created,
  );
  const socket = createHostSessionSocket(
    createDefaultApmeApiAdapter({ apiBase: "http://host", fetch }),
    "ws://gateway/session",
  );
  socket.close();
  expect(fetch.mock.calls[0]?.[1]?.signal).toBeUndefined();
  finish(new Response('{"id":"late"}'));
  await vi.waitFor(() => expect(deleted.cancel).toHaveBeenCalledOnce());
  expect(fetch).toHaveBeenCalledTimes(2);
});
