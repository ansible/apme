import { ConfigReader } from "@backstage/config";
import { InputError } from "@backstage/errors";
import { proxyProjectOperation } from "./gatewayOperationProxy";

const rootConfig = new ConfigReader({
  ansible: { apme: { enabled: true, baseUrl: "https://gateway.test" } },
});
const logger = { debug: jest.fn() } as never;

function responseSink() {
  const chunks: Buffer[] = [];
  return {
    chunks,
    response: {
      status: jest.fn().mockReturnThis(),
      setHeader: jest.fn(),
      write: jest.fn((chunk: Buffer) => {
        chunks.push(chunk);
        return true;
      }),
      end: jest.fn(),
    },
  };
}

describe("gateway operation proxy", () => {
  afterEach(() => jest.restoreAllMocks());

  it.each([
    "/../admin",
    "/%2e%2e/admin",
    "/%252e%252e/admin",
    "/%2fadmin",
    "/%5cadmin",
  ])(
    "rejects traversal suffix %s before contacting Gateway",
    async (operationSuffix) => {
      const fetchSpy = jest.spyOn(global, "fetch").mockResolvedValue({} as Response);
      const { response } = responseSink();
      await expect(
        proxyProjectOperation({
          req: {
            method: "GET",
            url: "/apme/projects/p/operation",
            headers: {},
          },
          res: response,
          rootConfig,
          logger,
          projectId: "p",
          operationSuffix,
        }),
      ).rejects.toBeInstanceOf(InputError);
      expect(fetchSpy).not.toHaveBeenCalled();
    },
  );

  it("streams non-SSE Gateway bodies and forwards client cancellation", async () => {
    let abortRequest: (() => void) | undefined;
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("first"));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetchSpy = jest.spyOn(global, "fetch").mockResolvedValue({
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      body,
    } as Response);
    const { chunks, response } = responseSink();
    const operation = proxyProjectOperation({
      req: {
        method: "GET",
        url: "/apme/projects/p/operation",
        headers: {},
        on: (_event, listener) => {
          abortRequest = listener;
        },
      },
      res: response,
      rootConfig,
      logger,
      projectId: "p",
      operationSuffix: "",
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(response.write).toHaveBeenCalledWith(Buffer.from("first"));
    abortRequest?.();
    await operation;

    expect((init?.signal as AbortSignal).aborted).toBe(true);
    expect(cancelled).toBe(true);
    expect(Buffer.concat(chunks).toString()).toBe("first");
    expect(response.end).toHaveBeenCalledTimes(1);
  });
});
